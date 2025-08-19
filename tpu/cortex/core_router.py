# cortex/core_router.py
from __future__ import annotations
import logging
from typing import Any, Dict, Optional

# --- token ledger (best-effort; safe fallbacks) ---------------------------
try:
    from core.token_ledger import upsert_event, get_context
except Exception:  # pragma: no cover
    def upsert_event(*args, **kwargs):  # type: ignore
        pass
    def get_context(token: str) -> Dict[str, Any]:  # type: ignore
        return {}

# --- supervisor (inject this at bootstrap) --------------------------------
try:
    from cortex.core_supervisor import CoreSupervisor
except Exception:  # pragma: no cover
    class CoreSupervisor:  # type: ignore
        def evaluate(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
            return {"final_score": 0.0, "action": "ignore", "reasoning": [], "insights": {}}

supervisor: Optional[CoreSupervisor] = None  # set me in your main/bootstrap

# -------------------------------------------------------------------------


def _token_key(ev: Dict[str, Any]) -> Optional[str]:
    """Pick a stable token key; prefer on-chain identifiers."""
    return ev.get("token") or ev.get("mint") or ev.get("token_address")


def _safe_upsert(token: str, *, source: str, payload: Dict[str, Any], bucket: Optional[str] = None, ts_iso: Optional[str] = None):
    """
    Call upsert_event with a tolerant signature so we don't explode if the
    implementation uses different param names. We try a couple of shapes.
    """
    if not payload:
        return
    try:
        if bucket is not None:
            upsert_event(token, source=source, payload={**payload, "_bucket": bucket}, ts_iso=ts_iso)  # common impl
        else:
            upsert_event(token, source=source, payload=payload, ts_iso=ts_iso)
    except TypeError:
        # Alternate shape: upsert_event(token, bucket=..., source=..., data=...)
        try:
            kw = {"source": source, "data": payload}
            if bucket is not None:
                kw["bucket"] = bucket
            if ts_iso:
                kw["ts_iso"] = ts_iso
            upsert_event(token, **kw)  # type: ignore
        except Exception:
            pass
    except Exception:
        pass


async def handle_event(event: Dict[str, Any]):
    """
    Unified router entrypoint.

    Minimal event schema (extras are fine):
      - token / mint / token_address (str)  ← REQUIRED for token-level scoring
      - action (str)
      - source (str)                        e.g. 'telegram_group', 'crawler'
      - wallets (list)                      optional
      - tx (dict)                           optional
      - meta (dict)                         optional
      - messages (list[str|dict])           optional (social slice)
    """
    token = _token_key(event)
    if not token:
        logging.info("[Cortex] Dropping event without token/mint.")
        return

    action   = event.get("action")
    source   = event.get("source", "unknown")
    wallets  = event.get("wallets", [])
    tx       = event.get("tx", {}) or {}
    meta     = event.get("meta", {}) or {}
    messages = event.get("messages", []) or []

    logging.info(f"[Cortex] Routing '{action}' for {token} | wallets={len(wallets)} src={source}")

    # 1) Persist raw slices into the ledger so all producers enrich the SAME token record.
    try:
        if meta:
            _safe_upsert(token, source=source, payload=meta, bucket="meta")
        if tx:
            # store latest tx & bump a cheap txn counter bucket
            ts_iso = tx.get("ts") or tx.get("timestamp")
            _safe_upsert(token, source=source, payload={"last_tx": tx}, bucket="txn", ts_iso=ts_iso)
            _safe_upsert(token, source=source, payload={"count": 1}, bucket="txn_counter")
        if messages:
            _safe_upsert(token, source=source, payload={"last_messages": messages}, bucket="social")
        if wallets:
            _safe_upsert(token, source=source, payload={"wallets": wallets}, bucket="wallets")
    except Exception as e:
        logging.warning(f"[Cortex] ledger upsert failed: {e}")

    # 2) Pull the assembled context and run a single evaluate() for this token.
    try:
        ctx = get_context(token) or {}
        # normalize minimal fields many cortices expect
        ctx.setdefault("mint", token)
        ctx.setdefault("token_address", token)
        if "meta" not in ctx and meta:
            ctx["meta"] = meta
        if "recent_txns" not in ctx and tx:
            # some txn cortices accept a pre-baked list
            ctx["recent_txns"] = [tx]

        if supervisor is None:
            logging.warning("[Cortex] supervisor is not set; skipping evaluation.")
            return

        result = supervisor.evaluate(ctx)  # supervisor handles writing scores to ledger
        return result

    except Exception as e:
        logging.warning(f"[Cortex] Subsystem routing failed: {e}")
        return {"final_score": 0.0, "action": "ignore", "error": str(e)}
