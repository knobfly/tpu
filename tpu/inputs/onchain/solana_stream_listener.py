# inputs/onchain/solana_stream_listener.py
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta

import websockets
from core.live_config import config
from inputs.onchain.stream_event_classifier import classify_stream_event
from inputs.onchain.stream_safety_sentinel import StreamSafetySentinel
from librarian.data_librarian import librarian
from memory.token_memory_index import update_pool_params, update_pool_snapshot
from solana.rpc.async_api import AsyncClient
from utils.logger import log_event
from utils.orca_sdk import get_pool_accounts_for_mint as orca_get_pool
from utils.raydium_sdk import get_pool_accounts_for_mint as raydium_get_pool
from utils.rpc_loader import get_active_rpc
from utils.service_status import update_status
from utils.solana_balances import get_token_account_ui_amount

try:
    from defense.safety_sentinel import safety as GLOBAL_SAFETY
except Exception:
    GLOBAL_SAFETY = None

# =======================
# Connection / RPC basics
# =======================
MAINNET_WS = "wss://api.mainnet-beta.solana.com"

# Raydium / Orca program IDs (v4 + whirlpools classic)
RAYDIUM_PROGRAMS = {
    # AMM v4
    "RVKd61ztZW9mq8wCnD1iDfbTtyjcibGZ2y3sPzoiUJq",
    # CLMM / OpenBook router variant (if you use it in logs)
    "9tdctL2kJHREvJBCXJhfpapM7pWxEt49RdcMGsTCTQnD",
}
ORCA_PROGRAMS = {
    # Orca Whirlpool
    "whirLbMiicVdio4qvUfM5KAg6otUQ8m77Tqj7orGLfF",
    # Orca token-swap legacy
    "SWPpA9gE5R9y3v7v7k5c5hQfA2u4BSy1B9f1wE5GQ2Z",
}

ORCA_WHIRLPOOL_PROGRAM = "whirLbMiicVdio4qvUfM5KAg6G7VL6qJp6Zt6Z4aMDS"  # Orca Whirlpool (main)
RAYDIUM_AMM_V4        = "675kPX9MHTjS2zt1qfr1NYHqWgJz7jzH6tK5g84n4hS"   # Raydium AMM v4


# Default commitment, overridable via live_config["stream_subscriptions"]["commitment"]
def _commitment_dict():
    cm = (config.get("stream_subscriptions", {}) or {}).get("commitment", "processed")
    return {"commitment": cm}

PING_INTERVAL = 20
PING_TIMEOUT = 20

# =======================
# Dynamic Stream Controls
# =======================
STREAM_CTRL = asyncio.Queue()
STREAM_SAFETY = StreamSafetySentinel()  # <— NEW local safety (WS-facing)

# Persisted desired subs (re-applied on reconnect)
DESIRED = {
    "accounts": set(),      # pubkeys
    "signatures": set(),    # tx signatures
    "mentions": set(),      # pubkeys mentioned in logs
    "programs": set(),      # program IDs for program/account owner watch
}

# Active subscription map: sub_id -> meta
ACTIVE = {}  # e.g. { 1234: {"type":"account","label":<pubkey>} }
# Pending id map: req_id -> meta (for matching "result" to what we asked)
PENDING = {}

# very small throttle so we don't hammer RPC
_last_vault_probe = {}

# --- simple debounce / rate-limit maps for safety emits ---
_last_emit = {
    "lp_unlock": {},       # token -> ts
    "vault_drain": {},     # token -> ts
    "honeypot": {},        # token -> ts
}
EMIT_COOLDOWN_S = 120  # per token, per hazard

def _rid() -> int:
    return int(time.time() * 1000) & 0x7FFFFFFF

async def _send_req(ws, method: str, params, meta: dict):
    req_id = _rid()
    PENDING[req_id] = meta
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}))

def _handle_rpc_ack(msg: dict):
    rid = msg.get("id")
    if rid in PENDING and "result" in msg:
        meta = PENDING.pop(rid)
        sub_id = msg["result"]
        ACTIVE[sub_id] = meta

async def _apply_all_desired(ws):
    """Re-issue everything after reconnect."""
    c = _commitment_dict()
    for acc in list(DESIRED["accounts"]):
        await _send_req(ws, "accountSubscribe", [acc, c], {"type": "account", "label": acc})
    for sig in list(DESIRED["signatures"]):
        await _send_req(ws, "signatureSubscribe", [sig, c], {"type": "signature", "label": sig})
    for pk in list(DESIRED["mentions"]):
        await _send_req(ws, "logsSubscribe", [{"mentions": [pk]}, c], {"type": "logs_mention", "label": pk})
    for prog in list(DESIRED["programs"]):
        await _send_req(ws, "programSubscribe", [prog, c], {"type": "program", "label": prog})

async def _drain_ctrl(ws):
    """Apply queued add/remove requests without reconnecting."""
    c = _commitment_dict()
    try:
        while True:
            cmd = STREAM_CTRL.get_nowait()
            op = cmd.get("op")
            label = cmd.get("value")
            if op == "acct_sub":
                if label not in DESIRED["accounts"]:
                    DESIRED["accounts"].add(label)
                    await _send_req(ws, "accountSubscribe", [label, c], {"type": "account", "label": label})
            elif op == "acct_unsub":
                for sub_id, meta in list(ACTIVE.items()):
                    if meta.get("type") == "account" and meta.get("label") == label:
                        await _send_req(ws, "accountUnsubscribe", [sub_id], {"type": "account_unsub", "label": label})
                        ACTIVE.pop(sub_id, None)
                        break
                DESIRED["accounts"].discard(label)
            elif op == "sig_sub":
                if label not in DESIRED["signatures"]:
                    DESIRED["signatures"].add(label)
                    await _send_req(ws, "signatureSubscribe", [label, c], {"type": "signature", "label": label})
            elif op == "sig_unsub":
                for sub_id, meta in list(ACTIVE.items()):
                    if meta.get("type") == "signature" and meta.get("label") == label:
                        await _send_req(ws, "signatureUnsubscribe", [sub_id], {"type": "signature_unsub", "label": label})
                        ACTIVE.pop(sub_id, None)
                        break
                DESIRED["signatures"].discard(label)
            elif op == "mention_sub":
                if label not in DESIRED["mentions"]:
                    DESIRED["mentions"].add(label)
                    await _send_req(ws, "logsSubscribe", [{"mentions": [label]}, c], {"type": "logs_mention", "label": label})
            elif op == "mention_unsub":
                for sub_id, meta in list(ACTIVE.items()):
                    if meta.get("type") == "logs_mention" and meta.get("label") == label:
                        await _send_req(ws, "logsUnsubscribe", [sub_id], {"type": "logs_unsub", "label": label})
                        ACTIVE.pop(sub_id, None)
                        break
                DESIRED["mentions"].discard(label)
            elif op == "program_sub":
                if label not in DESIRED["programs"]:
                    DESIRED["programs"].add(label)
                    await _send_req(ws, "programSubscribe", [label, c], {"type": "program", "label": label})
            elif op == "program_unsub":
                for sub_id, meta in list(ACTIVE.items()):
                    if meta.get("type") == "program" and meta.get("label") == label:
                        await _send_req(ws, "programUnsubscribe", [sub_id], {"type": "program_unsub", "label": label})
                        ACTIVE.pop(sub_id, None)
                        break
                DESIRED["programs"].discard(label)
    except asyncio.QueueEmpty:
        return

# Public helpers you can import elsewhere (TradeExecutor uses these)
async def request_account_watch(account: str):
    await STREAM_CTRL.put({"op": "acct_sub", "value": account})

async def stop_account_watch(account: str):
    await STREAM_CTRL.put({"op": "acct_unsub", "value": account})

async def request_signature_watch(signature: str):
    await STREAM_CTRL.put({"op": "sig_sub", "value": signature})

async def stop_signature_watch(signature: str):
    await STREAM_CTRL.put({"op": "sig_unsub", "value": signature})

async def request_logs_mention_watch(pubkey: str):
    await STREAM_CTRL.put({"op": "mention_sub", "value": pubkey})

async def stop_logs_mention_watch(pubkey: str):
    await STREAM_CTRL.put({"op": "mention_unsub", "value": pubkey})

async def request_program_watch(program_id: str):
    await STREAM_CTRL.put({"op": "program_sub", "value": program_id})

async def stop_program_watch(program_id: str):
    await STREAM_CTRL.put({"op": "program_unsub", "value": program_id})

async def _maybe_probe_vaults(token_mint: str, pool: dict):
    now = time.time()
    key = f"{token_mint}:{pool.get('state')}"
    if now - _last_vault_probe.get(key, 0) < 15:  # 15s min
        return
    _last_vault_probe[key] = now

    vault_a, vault_b = pool.get("vault_a"), pool.get("vault_b")
    if not (vault_a and vault_b):
        return

    try:
        async with AsyncClient(get_active_rpc()) as c:
            a = await c.get_token_account_balance(vault_a)
            b = await c.get_token_account_balance(vault_b)

        def _to_sol(resp):
            try:
                ui = (resp.value or {}).get("uiAmount")
                return float(ui) if ui is not None else None
            except Exception:
                return None

        va = _to_sol(a)
        vb = _to_sol(b)
        if va is not None and vb is not None:
            snap = {"vault_a_sol": va, "vault_b_sol": vb, "ts": time.time()}
            update_pool_snapshot(token_mint, snap)
            # feed safety sentinel
            try:
                await safety.observe_pool_update(
                    token=token_mint,
                    vault_a_sol=va,
                    vault_b_sol=vb,
                    also_check_lp=True,
                    also_check_honeypot=False,
                )
            except Exception:
                pass
    except Exception as e:
        logging.debug(f"[Stream] vault probe failed: {e}")

async def _try_pool_observation_from_program_event(ev: dict):
    """
    Best-effort: when we see a program account notification for Raydium/Orca,
    attempt to resolve pool vaults and push a safety observation.
    """
    try:
        raw = ev.get("raw") or {}
        owner = ((raw.get("account") or {}).get("owner")) or ev.get("owner")
        if owner not in RAYDIUM_PROGRAMS | ORCA_PROGRAMS:
            return

        # Guess the token mint from the account if present (not always there).
        # If not present, you can skip; the safety layer still gets pool-level signals.
        token_mint = None
        # Some RPCs include parsed data; if you have it, extract mint here.

        get_pool = raydium_get_pool if owner in RAYDIUM_PROGRAMS else orca_get_pool

        # FAST PATH: if your chart/librarian layer has a "focus mint", you can pass that instead.
        # Otherwise this relies on later scoring flows to feed mint-level hooks.

        # We can still emit pool health if we find the pool and vaults:
        # (if token_mint is unknown here, we emit with None—stream_safety can still track vault behavior)
        pool = None
        # If you *do* know a specific mint (e.g., from your trade route), call get_pool(mint) directly
        # For generic program notifications without mint, just skip (we can’t map which mint to check).
        if token_mint:
            pool = await get_pool(token_mint)
        if not pool:
            return

        vA = pool.get("vault_a")
        vB = pool.get("vault_b")
        if not (vA and vB):
            return

        balA = await get_token_account_ui_amount(vA)
        balB = await get_token_account_ui_amount(vB)
        if balA is None or balB is None:
            return

        # Call your safety sentinel
        await safety.observe_pool_update(
            token=token_mint or pool.get("baseMint") or pool.get("quoteMint"),
            vault_a_sol=float(balA),
            vault_b_sol=float(balB),
            also_check_lp=True,
            also_check_honeypot=False,
        )
    except Exception as e:
        logging.debug(f"[SolanaStream] pool observe skipped: {e}")

# =======================
# Bootstrap desired subs
# =======================
def _preload_desired_from_config():
    """Load initial targets from live_config['stream_subscriptions'] and add Raydium/Orca programs."""
    ss = config.get("stream_subscriptions", {}) or {}
    for k, bucket in (("accounts","accounts"),("signatures","signatures"),("mentions","mentions"),("programs","programs")):
        vals = ss.get(k, []) or []
        if isinstance(vals, list):
            DESIRED[bucket].update(v for v in vals if isinstance(v, str) and len(v) > 20)
    # Always watch AMM programs on mainnet
    DESIRED["programs"].update(RAYDIUM_PROGRAMS)
    DESIRED["programs"].update(ORCA_PROGRAMS)

# =======================
# Event parsing / routing
# =======================
def _extract_wallets_from_logs(logs):
    wallets = set()
    for logline in logs or []:
        for word in logline.split():
            if word.isalnum() and 32 <= len(word) <= 44:
                wallets.add(word)
    return list(wallets)

def _extract_tokens_from_logs(logs):
    tokens = set()
    for logline in logs or []:
        if "mint" in logline.lower():
            for word in logline.split():
                if word.isalnum() and 32 <= len(word) <= 44:
                    tokens.add(word)
    return list(tokens)

def _normalize_event_from_logs(msg_value: dict) -> dict:
    signature = msg_value.get("signature")
    logs = msg_value.get("logs", [])
    wallets = _extract_wallets_from_logs(logs)
    tokens = _extract_tokens_from_logs(logs)
    return {
        "type": "solana_log",
        "signature": signature,
        "program_id": None,
        "slot": msg_value.get("slot"),
        "wallets": wallets,
        "tokens": tokens,
        "logs": logs,
        "timestamp": datetime.utcnow().isoformat(),
        "raw": msg_value,
    }

def _normalize_event_from_account(pubkey: str, account_info: dict, ctx_slot: int) -> dict:
    return {
        "type": "account_update",
        "account": pubkey,
        "slot": ctx_slot,
        "owner": ((account_info or {}).get("owner")),
        "lamports": ((account_info or {}).get("lamports")),
        "data_len": len(((account_info or {}).get("data") or {}).get("data", [])) if isinstance(account_info.get("data"), dict) else None,
        "timestamp": datetime.utcnow().isoformat(),
        "raw": account_info,
    }

def _normalize_event_from_signature(sig: str, status: dict, ctx_slot: int) -> dict:
    return {
        "type": "signature_update",
        "signature": sig,
        "slot": ctx_slot,
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "raw": status,
    }

def _normalize_event_from_program(key: str, acc: dict, ctx_slot: int) -> dict:
    return {
        "type": "program_account",
        "program": key,
        "slot": ctx_slot,
        "account": (acc or {}).get("pubkey"),
        "owner": ((acc or {}).get("account", {}) or {}).get("owner"),
        "lamports": ((acc or {}).get("account", {}) or {}).get("lamports"),
        "timestamp": datetime.utcnow().isoformat(),
        "raw": acc,
    }

async def _route_event(event: dict):
    """Send to Librarian + classifier + stream safety hooks."""

    # 1) Persist/learn
    try:
        await librarian.ingest_stream_event(event)
    except Exception as e:
        logging.warning(f"[SolanaStream] Librarian ingest failed: {e}")
    try:
        await classify_stream_event(event)
    except Exception as e:
        logging.warning(f"[SolanaStream] Classifier failed: {e}")


    # 2) ML Model Inference (price, rug, wallet) and propagate predictions
    try:
        from ml.price_predictor import PricePredictor
        from ml.rug_detector import RugDetector
        from ml.wallet_behavior_model import WalletBehaviorModel
        import torch

        # Prepare features from event (customize for your real event schema)
        price_features = torch.tensor([[event.get('price', 0), event.get('volume', 0)]], dtype=torch.float32)
        rug_features = torch.tensor([[event.get('lp_locked', 0), event.get('honeypot', 0), event.get('blacklisted', 0), event.get('holders', 0)]], dtype=torch.float32)
        wallet_features = torch.tensor([[event.get('tx_count', 0), event.get('volume', 0), event.get('alpha_tag', 0)]], dtype=torch.float32)

        # Load models (ideally, load once and cache; here for demo)
        price_model = PricePredictor(input_dim=2)
        rug_model = RugDetector(input_dim=4)
        wallet_model = WalletBehaviorModel(input_dim=3)

        price_pred = price_model(price_features).item()
        rug_pred = rug_model(rug_features).item()
        wallet_pred = wallet_model(wallet_features).detach().numpy().tolist()[0]

        # Augment event with ML predictions
        event['ml_price_pred'] = price_pred
        event['ml_rug_pred'] = rug_pred
        event['ml_wallet_pred'] = wallet_pred

        log_event(f"[ML] Price prediction: {price_pred}")
        log_event(f"[ML] Rug risk: {rug_pred}")
        log_event(f"[ML] Wallet behavior: {wallet_pred}")
    except Exception as e:
        logging.debug(f"[SolanaStream] ML inference skipped: {e}")

    # 3) Cheap, log-driven safety heuristics
    try:
        await _safety_hooks_from_event(event)
    except Exception as e:
        logging.debug(f"[SolanaStream] safety hook skipped: {e}")

# -------------------------
# Safety: lightweight hooks
# -------------------------
def _cooldown_ok(kind: str, token: str, now_ts: float) -> bool:
    last = _last_emit[kind].get(token, 0)
    if now_ts - last >= EMIT_COOLDOWN_S:
        _last_emit[kind][token] = now_ts
        return True
    return False

async def _emit_hazard(kind: str, token: str, payload: dict | None = None):
    now_ts = time.time()
    if not _cooldown_ok(kind, token, now_ts):
        return
    # Prefer event_bus if available
    try:
        from runtime.event_bus import event_bus
        from runtime.event_bus import now as ev_now
        await event_bus().emit({
            "id": f"{kind}:{token}:{int(now_ts*1000)}",
            "ts": ev_now(),
            "type": kind,
            "token": token,
            "meta": payload or {},
        })
    except Exception:
        log_event(f"[StreamSafety] {kind} → {token} | {payload or {}}")

async def _safety_hooks_from_event(ev: dict):
    """
    Heuristic detectors:
      - logs mention patterns (remove_liquidity, withdraw, unlock)
      - optional async honeypot probe (debounced)
    """
    t = ev.get("type")
    if t == "solana_log":
        logs = ev.get("logs") or []
        tokens = ev.get("tokens") or []
        lowered = " ".join(logs).lower()

        # LP unlock-ish phrases seen in bot UIs / AMM logs
        if any(kw in lowered for kw in ("unlock lp", "lp unlock", "unlock-liquidity", "remove lock")):
            for mint in tokens:
                await _emit_hazard("lp_unlock", mint, {"signature": ev.get("signature")})

        # Vault drain heuristic: mass remove/withdraw/spend
        if any(kw in lowered for kw in ("remove_liquidity", "withdraw", "burn liquidity", "close position")):
            for mint in tokens:
                await _emit_hazard("vault_drain", mint, {"signature": ev.get("signature")})

        # Optional, cheap honeypot flag if your downstream tagged it in logs
        if "honeypot" in lowered:
            for mint in tokens:
                await _emit_hazard("honeypot_detected", mint, {"signature": ev.get("signature")})

    # Account/program updates: if you later decode vaults -> call STREAM_SAFETY.observe_pool_update(...)
    # Here we just leave the hook ready; you can wire real decoded balances when available.

# =======================
# Main streaming loop
# =======================
async def stream_solana_logs():
    update_status("solana_stream_listener")
    log_event("🌊 Solana Stream Listener starting...")

    _preload_desired_from_config()

    backoff = 1.5
    max_backoff = 30.0
    # --- Periodic summary state ---
    summary_last_emit = time.time()
    tokens_seen = set()
    wallets_seen = set()
    events_seen = 0

    while True:
        try:
            async with websockets.connect(MAINNET_WS, ping_interval=PING_INTERVAL, ping_timeout=PING_TIMEOUT, max_size=None) as ws:
                log_event("✅ Connected to Solana WS")
                await _apply_all_desired(ws)
                log_event("✅ Subscriptions applied")

                # main loop
                while True:
                    await _drain_ctrl(ws)
                    raw = await ws.recv()
                    data = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw

                    events_seen += 1

                    # --- Track tokens/wallets from events ---
                    if isinstance(data, dict) and "method" in data and "params" in data:
                        method = data["method"]
                        params = data["params"] or {}
                        value = params.get("result") or params.get("value") or {}
                        # Try to extract token/wallet info from logs/account/program events
                        if method == "logsNotification":
                            ev = _normalize_event_from_logs(value)
                            token = ev.get("token")
                            wallet = ev.get("wallet")
                            if token:
                                tokens_seen.add(token)
                            if wallet:
                                wallets_seen.add(wallet)
                        elif method == "accountNotification":
                            acc_info = (value or {}).get("value") or {}
                            token = acc_info.get("mint")
                            owner = acc_info.get("owner")
                            if token:
                                tokens_seen.add(token)
                            if owner:
                                wallets_seen.add(owner)
                        elif method == "programNotification":
                            ev = _normalize_event_from_program(params.get("subscription"), value, params.get("context", {}).get("slot"))
                            for t in ev.get("tokens", []):
                                tokens_seen.add(t)
                            for w in ev.get("wallets", []):
                                wallets_seen.add(w)

                    # --- Emit summary log every 15 seconds ---
                    now = time.time()
                    if now - summary_last_emit > 15:
                        log_event(f"[SolanaStream] Summary: {len(tokens_seen)} tokens, {len(wallets_seen)} wallets, {events_seen} events in last {int(now-summary_last_emit)}s.")
                        summary_last_emit = now
                        events_seen = 0

                    if isinstance(data, dict) and "result" in data and "id" in data:
                        _handle_rpc_ack(data)
                        continue

                    if isinstance(data, dict) and "method" in data and "params" in data:
                        method = data["method"]
                        params = data["params"] or {}
                        value = params.get("result") or params.get("value") or {}

                        if method == "logsNotification":
                            ev = _normalize_event_from_logs(value)
                            await _route_event(ev)
                            continue

                        if method == "accountNotification":
                            ctx_slot = ((value or {}).get("context") or {}).get("slot") or params.get("context", {}).get("slot")
                            acc_info = (value or {}).get("value") or {}
                            sub_id = params.get("subscription")
                            meta = ACTIVE.get(sub_id, {})
                            pubkey = meta.get("label")
                            ev = _normalize_event_from_account(pubkey, acc_info, ctx_slot)
                            await _route_event(ev)
                            continue

                        if method == "signatureNotification":
                            ctx_slot = (params.get("context") or {}).get("slot")
                            status = value
                            sub_id = params.get("subscription")
                            meta = ACTIVE.get(sub_id, {})
                            sig = meta.get("label")
                            ev = _normalize_event_from_signature(sig, status, ctx_slot)
                            await _route_event(ev)
                            continue

                        if method == "programNotification":
                            ctx_slot = (params.get("context") or {}).get("slot")
                            sub_id = params.get("subscription")
                            meta = ACTIVE.get(sub_id, {})
                            prog = meta.get("label")
                            ev = _normalize_event_from_program(prog, value, ctx_slot)

                            if prog in (ORCA_WHIRLPOOL_PROGRAM, RAYDIUM_AMM_V4):
                                token_mint = None
                                pool_meta = None
                                try:
                                    if prog == ORCA_WHIRLPOOL_PROGRAM:
                                        from utils.orca_sdk import get_pool_accounts_for_mint
                                    else:
                                        from utils.raydium_sdk import get_pool_accounts_for_mint

                                    cand_mints = set(ev.get("tokens") or [])
                                    for m in list(cand_mints)[:3]:
                                        try:
                                            p = await get_pool_accounts_for_mint(m)
                                            if p:
                                                token_mint = m
                                                pool_meta = p
                                                break
                                        except Exception:
                                            continue

                                    if token_mint and pool_meta:
                                        params = {
                                            "amm": "orca" if prog == ORCA_WHIRLPOOL_PROGRAM else "raydium",
                                            "pool_state": pool_meta.get("state"),
                                            "fee_bps": pool_meta.get("fee_bps", 0),
                                            "tick_spacing": pool_meta.get("tick_spacing", 0),
                                            "baseMint": pool_meta.get("baseMint"),
                                            "quoteMint": pool_meta.get("quoteMint"),
                                        }
                                        update_pool_params(token_mint, params)
                                        await _maybe_probe_vaults(token_mint, pool_meta)
                                except Exception as e:
                                    logging.debug(f"[Stream] pool mapping failed: {e}")

                            await _route_event(ev)
                            asyncio.create_task(_try_pool_observation_from_program_event(ev))
                            continue
            backoff = 1.5

        except websockets.InvalidStatusCode as wse:
            if wse.status_code == 429:
                logging.warning("[SolanaStream] HTTP 429 (rate limited). Backing off...")
                await asyncio.sleep(min(max_backoff, backoff))
                backoff = min(max_backoff, backoff * 1.8)
            else:
                logging.warning(f"[SolanaStream] InvalidStatusCode {wse.status_code}. Reconnecting...")
                await asyncio.sleep(min(max_backoff, backoff))
                backoff = min(max_backoff, backoff * 1.5)

        except Exception as e:
            logging.warning(f"[SolanaStream] Error: {e}")
            await asyncio.sleep(min(max_backoff, backoff))
            backoff = min(max_backoff, backoff * 1.5)

# === Entrypoint for use in async loop ===
async def run_solana_stream_listener():
    await stream_solana_logs()
