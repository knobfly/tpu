# /stop_snipe_defender.py
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Union, Dict, List, Any

from core.live_config import config
from defense.honeypot_similarity_scanner import save_new_rug_pattern
from inputs.wallet.wallet_reputation_tracker import get_wallet_rug_score
from memory.token_outcome_memory import get_recent_failed_trades
from utils.fetch_bytecode import fetch_contract_bytecode
from utils.logger import log_event

# --- Paths ---
DATA_DIR = Path("/home/ubuntu/nyx/runtime/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

BLOCK_FILE = DATA_DIR / "snipe_block.json"
RUG_LOG_FILE = DATA_DIR / "rug_events.json"

# --- Tunables / heuristics ---
RUG_LIMIT = 2
RUG_WINDOW_MINUTES = 5
MANUAL_STOP_DURATION_MINUTES = 10

# Legacy-compatible knobs (used by get_rug_rate)
RUG_TIME_WINDOW_MINUTES = 120   # how far back to look
RUG_THRESHOLD = 5               # how many recent rugs = danger
REPUTATION_RISK_WEIGHT = 3.0    # penalize bad wallets

_stop_snipe_mode = False
_stop_snipe_start_time: Optional[datetime] = None

# =============================================================================
# File-Based Snipe Block
# =============================================================================

def _load_block_data() -> Dict[str, Any]:
    try:
        if not BLOCK_FILE.exists():
            return {}
        with BLOCK_FILE.open("r") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _save_block_data(data: Dict[str, Any]) -> None:
    try:
        with BLOCK_FILE.open("w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log_event(f"[StopSnipe] Failed to persist block file: {e}")

def block_snipe_for(duration_minutes: int, reason: str = "Manual override") -> None:
    until = (datetime.utcnow() + timedelta(minutes=duration_minutes)).isoformat()
    _save_block_data({"blocked_until": until, "reason": reason})
    log_event(f"🚫 Snipe blocked for {duration_minutes} minutes. Reason: {reason}")

def unblock_snipe() -> None:
    try:
        if BLOCK_FILE.exists():
            BLOCK_FILE.unlink()
            log_event("✅ Snipe block removed manually.")
    except Exception as e:
        log_event(f"[StopSnipe] Failed to remove block file: {e}")

def is_file_blocked() -> bool:
    data = _load_block_data()
    iso = data.get("blocked_until")
    if not iso:
        return False
    try:
        return datetime.utcnow() < datetime.fromisoformat(iso)
    except Exception:
        return False

def get_block_reason() -> str:
    data = _load_block_data()
    return data.get("reason", "Unknown") if is_file_blocked() else "Not blocked"

# =============================================================================
# Rug-Rate (Backward-Compatible API)
# =============================================================================

def _safe_len(x) -> int:
    try:
        return len(x) if x is not None else 0
    except Exception:
        return 0

def get_rug_rate(
    token_context: Optional[dict] = None,
    *,
    since_minutes: Optional[int] = None,
    minutes: Optional[int] = None,
    return_details: bool = False,
) -> Union[float, Dict[str, Any]]:
    """
    New usage:
        rug_rate = get_rug_rate(minutes=10)  # float in [0,1]

    Legacy usage (kept):
        details = get_rug_rate(token_context)                # dict
        details = get_rug_rate(token_context, return_details=True)

    Computes failure rate over window and mixes in creator reputation for detailed mode.
    """
    window_min = int(minutes or since_minutes or RUG_TIME_WINDOW_MINUTES)

    # recent failed trades
    failed = 0
    try:
        failed = _safe_len(get_recent_failed_trades(since_minutes=window_min))
    except TypeError:
        # legacy helper that takes minutes=
        failed = _safe_len(get_recent_failed_trades(minutes=window_min))
    except Exception:
        failed = 0

    # We may not have total trade count here; treat failure frequency as a rate proxy
    # If you have get_recent_trades, you could divide by total; we keep simple & stable.
    total = max(failed, 1)
    rate = min(max(failed / total, 0.0), 1.0)

    # Float path for schedulers, etc.
    if token_context is None and not return_details:
        return float(rate)

    # Detailed legacy dict (adds reasons & creator reputation)
    token_addr = (token_context or {}).get("token_address") if isinstance(token_context, dict) else None
    creator = (token_context or {}).get("creator") if isinstance(token_context, dict) else None

    risk_score = 0.0
    reasons: List[str] = []
    # Map rate to 0..10
    risk_score += min(rate * 10.0, 10.0)

    if failed >= RUG_THRESHOLD:
        risk_score += 5.0
        reasons.append(f"{failed} rugs in past {window_min}m")

    if creator:
        try:
            rep = float(get_wallet_rug_score(creator))
            if rep >= REPUTATION_RISK_WEIGHT:
                risk_score += rep
                reasons.append(f"creator rug rep: {rep:.1f}")
        except Exception:
            pass

    risk_score = float(min(risk_score, 10.0))

    return {
        "rug_rate": rate,
        "risk_score": risk_score,
        "reasons": reasons,
        "window_minutes": window_min,
        "failed_trades": failed,
        "token": token_addr,
    }

# =============================================================================
# Rug-Wave Defender (burst rugs in short window)
# =============================================================================

def _load_rug_events() -> List[str]:
    try:
        if not RUG_LOG_FILE.exists():
            return []
        with RUG_LOG_FILE.open("r") as f:
            data = json.load(f) or []
            return [str(x) for x in data]
    except Exception:
        return []

def _save_rug_events(events: List[Union[str, datetime]]) -> None:
    try:
        with RUG_LOG_FILE.open("w") as f:
            json.dump(
                [e.isoformat() if isinstance(e, datetime) else str(e) for e in events],
                f,
                indent=2,
            )
    except Exception as e:
        log_event(f"[StopSnipe] Failed to persist rug log: {e}")

def register_rug_event(token_address: str | None = None) -> None:
    now = datetime.utcnow()
    events_iso = _load_rug_events()

    # keep only recent ones within the short window
    recent: List[datetime] = []
    for iso in events_iso:
        try:
            t = datetime.fromisoformat(iso)
            if (now - t).total_seconds() <= RUG_WINDOW_MINUTES * 60:
                recent.append(t)
        except Exception:
            continue

    recent.append(now)
    _save_rug_events(recent)

    # 🧠 learn new rug pattern asynchronously (best-effort)
    if token_address:
        try:
            asyncio.create_task(save_new_rug_pattern(token_address, fetch_contract_bytecode))
        except Exception as e:
            log_event(f"⚠️ Failed to save rug pattern for {token_address}: {e}")

    if len(recent) >= RUG_LIMIT:
        block_snipe_for(RUG_WINDOW_MINUTES, reason="Rug wave detected")

# =============================================================================
# Stop-Snipe Logic
# =============================================================================

def _check_manual_reset() -> None:
    global _stop_snipe_mode, _stop_snipe_start_time
    if _stop_snipe_mode and _stop_snipe_start_time:
        elapsed = (datetime.utcnow() - _stop_snipe_start_time).total_seconds()
        if elapsed >= MANUAL_STOP_DURATION_MINUTES * 60:
            _stop_snipe_mode = False
            _stop_snipe_start_time = None
            log_event("🕒 Manual stop-snipe timer expired. Auto-resuming.")

def is_snipe_blocked() -> bool:
    _check_manual_reset()
    # config flag still respected
    if not bool(config.get("use_stop_snipe", True)):
        return False
    return _stop_snipe_mode or is_file_blocked()

def is_stop_snipe_mode() -> bool:
    return is_snipe_blocked()

def toggle_stop_snipe_mode() -> bool:
    global _stop_snipe_mode, _stop_snipe_start_time
    _stop_snipe_mode = not _stop_snipe_mode
    _stop_snipe_start_time = datetime.utcnow() if _stop_snipe_mode else None
    state = "ENABLED" if _stop_snipe_mode else "DISABLED"
    log_event(f"🛑 Stop-snipe mode manually toggled: {state}")
    return _stop_snipe_mode

# =============================================================================
# AI / External Trigger
# =============================================================================

def activate_stop_snipes(reason: str = "Triggered by AI") -> None:
    block_snipe_for(MANUAL_STOP_DURATION_MINUTES, reason=reason)
    log_event(f"🤖 Auto stop-snipes activated. Reason: {reason}")
