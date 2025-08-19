import asyncio
import json
import logging
import os
import time
from typing import Any, AsyncIterator, Dict, Optional

from core.live_config import config
from inputs.onchain.stream_safety_sentinel import StreamSafetySentinel
from librarian.data_librarian import librarian
from utils.logger import log_event


async def _tail_jsonl(path: str) -> AsyncIterator[Dict[str, Any]]:
    """
    Async tail of a jsonl file. Yields parsed dicts as they arrive.
    Will create the file if it doesn't exist yet.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        # create empty file to avoid FileNotFoundError
        open(path, "a").close()

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        # jump to end
        f.seek(0, os.SEEK_END)
        while True:
            pos = f.tell()
            line = f.readline()
            if not line:
                await asyncio.sleep(0.25)
                f.seek(pos)
                continue
            try:
                obj = json.loads(line.strip())
                if isinstance(obj, dict):
                    yield obj
            except Exception:
                # skip malformed lines
                continue


# ---------- Librarian iterator wrapper (if available) ----------
async def _iter_librarian_stream(event_types: Optional[list[str]] = None) -> AsyncIterator[Dict[str, Any]]:
    """
    If your Librarian exposes an async iterator for stream events, use it.
    Otherwise, this will raise and the caller should fallback to JSONL.
    """
    # You may already have something like: librarian.iter_stream_events(types=[...])
    # If not, comment this out and rely only on the JSONL tail.
    if not hasattr(librarian, "iter_stream_events"):
        raise RuntimeError("Librarian has no iter_stream_events()")
    async for evt in librarian.iter_stream_events(types=event_types):
        if isinstance(evt, dict):
            yield evt


# ---------- Shape mappers ----------
def _extract_pool_fields(evt: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """
    Try to normalize a pool update from various shapes into:
      { "vault_a_sol": float, "vault_b_sol": float }
    Returns None if fields aren’t present/derivable.
    """
    # 1) Direct SOL balances present
    for k in ("vault_a_sol", "vault_b_sol"):
        if k not in evt:
            break
    else:
        return {"vault_a_sol": float(evt["vault_a_sol"]), "vault_b_sol": float(evt["vault_b_sol"])}

    # 2) Raydium/Orca common raw fields (lamports + decimals)
    try:
        # This is intentionally flexible; adapt keys to your raw payload
        va = evt.get("vault_a") or evt.get("vaultA") or {}
        vb = evt.get("vault_b") or evt.get("vaultB") or {}
        lam_a = float(va.get("lamports") or va.get("balance") or 0)
        lam_b = float(vb.get("lamports") or vb.get("balance") or 0)

        # token decimals if known (fallback 9 for SOL-like)
        dec_a = int(va.get("decimals") or 9)
        dec_b = int(vb.get("decimals") or 9)

        # If these vaults are actually SOL lamports, 9 is correct.
        # If they’re SPL token amounts, this still normalizes to a unit scale.
        vault_a_sol = lam_a / (10 ** dec_a)
        vault_b_sol = lam_b / (10 ** dec_b)

        if vault_a_sol == 0 and vault_b_sol == 0:
            return None
        return {"vault_a_sol": float(vault_a_sol), "vault_b_sol": float(vault_b_sol)}
    except Exception:
        return None


def _extract_token_mint(evt: Dict[str, Any]) -> Optional[str]:
    # Try a few common keys
    for k in ("token", "token_mint", "mint", "base_mint", "pool_mint"):
        v = evt.get(k)
        if isinstance(v, str) and len(v) > 20:
            return v
    # Sometimes under nested pool
    pool = evt.get("pool") or {}
    for k in ("base_mint", "token_mint", "mint"):
        v = pool.get(k)
        if isinstance(v, str) and len(v) > 20:
            return v
    return None


# ---------- Main adapter loop ----------
async def run_stream_safety_adapter(poll_source: str = "auto", jsonl_path: Optional[str] = None):
    """
    Forwards pool updates from Librarian/jsonl into the StreamSafetySentinel.
    - poll_source:
        "librarian" -> use Librarian stream iterator
        "file"      -> tail a JSONL file (path from config or arg)
        "auto"      -> try Librarian, fall back to JSONL
    - jsonl_path: path to stream JSONL if using file fallback
    """
    sentinel = StreamSafetySentinel()
    cfg = config.get("stream_adapter", {})
    path = jsonl_path or cfg.get("jsonl_path") or "/home/ubuntu/nyx/runtime/logs/firehose_stream.jsonl"
    wanted_types = {"pool_update", "raydium_pool", "orca_pool"}  # adjust to your event type names

    log_event("🛰️ StreamSafetyAdapter online.")

    async def _iter_events():
        # choose source
        source = poll_source
        if source == "auto":
            source = "librarian" if librarian is not None and hasattr(librarian, "iter_stream_events") else "file"

        if source == "librarian":
            try:
                async for e in _iter_librarian_stream(event_types=list(wanted_types)):
                    yield e
                return
            except Exception as e:
                logging.warning(f"[StreamSafetyAdapter] Librarian stream unavailable: {e}. Falling back to file.")
                # fallback to file
        # file mode
        async for e in _tail_jsonl(path):
            # filter if your log has mixed event types
            etype = (e.get("type") or "").lower()
            if not etype:
                # some logs may use {"topic": "..."} or nested payload
                etype = (e.get("topic") or "").lower()
            if etype and etype not in wanted_types:
                continue
            yield e

    # pump loop
    async for evt in _iter_events():
        try:
            token = _extract_token_mint(evt) or ""
            if not token:
                continue

            fields = _extract_pool_fields(evt) or {}
            va = fields.get("vault_a_sol")
            vb = fields.get("vault_b_sol")
            if va is None or vb is None:
                continue

            await sentinel.observe_pool_update(
                token=token,
                vault_a_sol=va,
                vault_b_sol=vb,
                also_check_lp=True,           # cheap piggyback
                also_check_honeypot=False,    # toggle if you want
            )
        except Exception as e:
            logging.debug(f"[StreamSafetyAdapter] evt parse/forward failed: {e}")
        # small micro-yield so we don’t hog the loop
        await asyncio.sleep(0)
