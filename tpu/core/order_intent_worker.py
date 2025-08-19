import os, json, time, hashlib, importlib
from pathlib import Path
from librarian.data_librarian import librarian

NYX_ROOT = Path(os.environ.get("NYX_ROOT", "/home/ubuntu/nyx"))
ORDERS_DIR = NYX_ROOT / "runtime" / "orders"
INTENTS = ORDERS_DIR / "intents.jsonl"
EXECUTED = ORDERS_DIR / "executed.jsonl"
ERRORS = ORDERS_DIR / "errors.jsonl"
OFFSET = ORDERS_DIR / ".intents.offset"

EXECUTE_ENABLED = os.environ.get("NYX_EXECUTE", "0") == "1"

def _append_jsonl(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def _read_offset():
    try:
        return int(OFFSET.read_text().strip())
    except Exception:
        return 0

def _write_offset(n: int):
    OFFSET.parent.mkdir(parents=True, exist_ok=True)
    OFFSET.write_text(str(n), encoding="utf-8")

def _hash_intent(intent: dict) -> str:
    return hashlib.sha256(json.dumps(intent, sort_keys=True).encode("utf-8")).hexdigest()

def _preflight(ctx: dict):
    lf = (ctx.get("market") or {}).get("liquidity_flags") or {}
    if lf.get("lp_removed") or lf.get("locked") or lf.get("burned"):
        return False, "liquidity_flags_block"

    risk = ctx.get("risk") or {}
    bl = set(risk.get("blacklisted_tokens") or [])
    if ctx.get("mint") in bl:
        return False, "blacklisted_token"

    hp = risk.get("honeypot") or {}
    if hp.get("score", 0) >= 0.8 or hp.get("is_honeypot"):
        return False, "honeypot_risk"

    try:
        candles = (ctx.get("market") or {}).get("ohlcv") or []
        last = candles[-1] if candles else {}
        vol = float(last.get("volume", last.get("v", 0)) or 0)
        if vol <= 0:
            return False, "zero_volume_last_candle"
    except Exception:
        pass

    return True, "ok"

def _try_import(module, func):
    try:
        m = importlib.import_module(module)
        return getattr(m, func, None)
    except Exception:
        return None

def _execute_token(side: str, ctx: dict, mode: str):
    tries = [
        ("nyx.execution.tokens", "place_order"),
        ("nyx.engines.execution.tokens", "place_order"),
        ("nyx.execution.router", "place_token_order"),
    ]
    for mod, fn in tries:
        f = _try_import(mod, fn)
        if f:
            return f(mint=ctx.get("mint"), side=side, context=ctx, mode=mode)
    raise RuntimeError("no_token_executor_found")

def _execute_nft(side: str, ctx: dict, mode: str):
    tries = [
        ("nyx.execution.nfts", "place_nft_order"),
        ("nyx.engines.execution.nfts", "place_nft_order"),
        ("nyx.execution.router", "place_nft_order"),
    ]
    for mod, fn in tries:
        f = _try_import(mod, fn)
        if f:
            return f(context=ctx, side=side, mode=mode)
    raise RuntimeError("no_nft_executor_found")

def _safe_execute(intent: dict):
    t0 = time.time()
    typ = intent.get("type", "TOKEN")
    mode = intent.get("mode", "BUY")
    side = "buy" if mode in ("BUY","AGGRESSIVE_BUY","AUTO") else "sell"
    mint = intent.get("mint")

    ctx = librarian.build_context(mint, window_minutes=60)
    ok, reason = _preflight(ctx)
    if not ok:
        raise RuntimeError(f"preflight_block:{reason}")

    if not EXECUTE_ENABLED:
        _append_jsonl(EXECUTED, {
            "_ts": time.time(),
            "dry_run": True,
            "type": typ, "mint": mint, "mode": mode, "side": side,
            "notes": "NYX_EXECUTE not set; logged only",
            "ctx_keys": list(ctx.keys())
        })
        return

    if typ == "NFT":
        res = _execute_nft(side, ctx, mode)
    else:
        res = _execute_token(side, ctx, mode)

    _append_jsonl(EXECUTED, {
        "_ts": time.time(),
        "type": typ, "mint": mint, "mode": mode, "side": side,
        "result": res if isinstance(res, (dict, list, str, int, float, bool)) else "ok",
        "latency_ms": int((time.time() - t0)*1000)
    })

def order_intent_loop(sleep_time: float = 0.25):
    INTENTS.parent.mkdir(parents=True, exist_ok=True)
    if not INTENTS.exists():
        INTENTS.touch()

    offset = _read_offset()
    with INTENTS.open("r", encoding="utf-8") as f:
        f.seek(offset)
        while True:
            line = f.readline()
            if not line:
                time.sleep(sleep_time)
                continue
            try:
                intent = json.loads(line)
                intent.setdefault("_id", _hash_intent(intent))
                _safe_execute(intent)
            except Exception as e:
                _append_jsonl(ERRORS, {
                    "_ts": time.time(),
                    "error": str(e)[:400],
                    "line": line.strip()[:800]
                })
            finally:
                _write_offset(f.tell())
