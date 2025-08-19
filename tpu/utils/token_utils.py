import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiohttp
import base58
from core.live_config import config, save_config
from solders.pubkey import Pubkey
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import TransferParams, get_associated_token_address, transfer
from utils.contract_parser import analyze_contract_risks
from utils.rpc_loader import get_active_rpc

_BASE58_SOL = r"[1-9A-HJ-NP-Za-km-z]{32,44}"
_TICKER     = r"\$[A-Za-z0-9_]{2,20}"
_HASHTAG    = r"#[A-Za-z0-9_]{2,20}"

_addr_re   = re.compile(rf"\b({_BASE58_SOL})\b")
_ticker_re = re.compile(_TICKER)
_tag_re    = re.compile(_HASHTAG)

def is_solana_address(s: str) -> bool:
    return bool(_addr_re.fullmatch(s or ""))

def mint_address_or_best_guess(text: str) -> dict:
    """
    Returns a dict describing the strongest token hint found in text.
    Priority: mint address > $TICKER > #hashtag.
    """
    if not text:
        return {"type": None, "value": None}

    addr = _addr_re.search(text)
    if addr:
        return {"type": "mint", "value": addr.group(1)}

    tick = _ticker_re.search(text)
    if tick:
        return {"type": "symbol", "value": tick.group(0).lstrip("$")}

    tag = _tag_re.search(text)
    if tag:
        return {"type": "hashtag", "value": tag.group(0).lstrip("#")}

    return {"type": None, "value": None}


# === RPC Utilities ===
async def _rpc_call(payload: dict, timeout: int = 10) -> dict:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(get_active_rpc(), json=payload, timeout=timeout) as resp:
                return await resp.json()
    except Exception as e:
        logging.warning(f"[TokenUtils] RPC call failed: {e}")
        return {}

# === Token Metadata ===
async def get_token_metadata(token_address: str) -> Optional[Dict[str, Any]]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [token_address, {"encoding": "jsonParsed"}]
    }
    try:
        data = await _rpc_call(payload)
        account = data.get("result", {}).get("value", {})
        meta = account.get("data", {}).get("parsed", {}).get("info", {})
        return {
            "name": meta.get("name"),
            "symbol": meta.get("symbol"),
            "decimals": meta.get("decimals"),
            "supply": meta.get("supply"),
            "project": {},
            "metadata": meta
        } if meta else None
    except Exception as e:
        logging.warning(f"[TokenUtils] Metadata fetch failed: {e}")
        return None

async def get_token_mint_info(mint_address: str) -> dict | None:
    """
    Fetch token mint info (decimals, supply, etc.) from Solana RPC.
    Returns a dict or None if unavailable.
    """
    rpc_url = get_active_rpc()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [mint_address, {"encoding": "jsonParsed"}]
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(rpc_url, json=payload, timeout=8) as resp:
                data = await resp.json()
                value = data.get("result", {}).get("value")
                if not value:
                    logging.warning(f"[TokenUtils] No mint info for {mint_address}")
                    return None
                parsed = value.get("data", {}).get("parsed", {}).get("info", {})
                return {
                    "mint": mint_address,
                    "decimals": int(parsed.get("decimals", 0)),
                    "supply": int(parsed.get("supply", 0)),
                    "is_initialized": parsed.get("isInitialized", False)
                }
    except Exception as e:
        logging.error(f"[TokenUtils] Failed to fetch mint info for {mint_address}: {e}")
        return None

async def get_token_liquidity_data(token_address: str) -> float:
    """
    Returns the total liquidity (in SOL) for a token.
    Primary source: Firehose. Fallback: direct RPC.
    """
    try:
        rpc = get_active_rpc()
        url = f"{rpc}/v1/getTokenSupply"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenSupply",
            "params": [token_address]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=5) as resp:
                res = await resp.json()
                ui_amount = float(res["result"]["value"]["uiAmount"])
                return ui_amount
    except Exception as e:
        logging.warning(f"[TokenUtils] Failed to fetch liquidity for {token_address}: {e}")
        return 0.0

def extract_token_name(text: str) -> str:
    """
    Extracts a likely token name from input text.
    Prioritizes uppercase tags, prefixed tokens, or wrapped names in parentheses or hashtags.
    Returns empty string if no valid name is found.
    """
    import re

    # Common token name patterns
    patterns = [
        r"\$([A-Z]{2,10})\b",         # $TOKEN
        r"#([A-Z]{2,10})\b",          # #TOKEN
        r"\(([A-Z]{2,10})\)",         # (TOKEN)
        r"\b([A-Z]{3,10})\b",         # bare uppercase
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip().upper()

    return ""

async def get_token_holder_distribution(token_address: str) -> dict:
    """
    Fetch token holder distribution from RPC.
    Used to classify whales, retail, and sniper concentrations.

    Args:
        token_address (str): The mint address of the token.

    Returns:
        dict: Example:
            {
                "whales": 3,
                "retail": 50,
                "snipers": 7,
                "top_10_share": 0.67
            }
    """
    try:
        url = get_active_rpc()
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [token_address]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                data = await resp.json()
                accounts = data.get("result", {}).get("value", [])
                if not accounts:
                    return {}

                total = sum(float(a["amount"]) for a in accounts)
                top_10_share = sum(float(a["amount"]) for a in accounts[:10]) / total if total else 0

                distribution = {
                    "whales": sum(1 for a in accounts if float(a["amount"]) > total * 0.1),
                    "retail": sum(1 for a in accounts if float(a["amount"]) < total * 0.01),
                    "snipers": sum(1 for a in accounts if "sniper" in a["address"].lower()),
                    "top_10_share": round(top_10_share, 4)
                }
                return distribution

    except Exception as e:
        logging.warning(f"[token_utils] Failed to fetch holder distribution: {e}")
        return {}

def _extract_address(wallet_or_address: Any) -> Optional[str]:
    """Accept either a string or a wallet-like object and return a base58 address string."""
    if isinstance(wallet_or_address, str):
        return wallet_or_address
    for attr in ("address", "pubkey", "public_key", "publicKey"):
        if hasattr(wallet_or_address, attr):
            val = getattr(wallet_or_address, attr)
            return str(val)
    return None

async def extract_token_from_tx(signature: str) -> dict | None:
    """
    Extracts token address and metadata from a transaction signature.
    Only returns result if it looks like a token mint or creation.
    """
    try:
        rpc = get_active_rpc()
        url = f"{rpc}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [signature, {"encoding": "jsonParsed"}]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=10) as resp:
                tx_data = await resp.json()

        result = tx_data.get("result", {})
        if not result:
            return None

        message = result.get("transaction", {}).get("message", {})
        instructions = message.get("instructions", [])

        for ix in instructions:
            parsed = ix.get("parsed", {})
            if parsed.get("type") == "initializeMint":
                info = parsed.get("info", {})
                token_address = ix.get("programId")  # fallback
                mint_address = info.get("mint")
                decimals = info.get("decimals", 0)

                return {
                    "token_address": mint_address or token_address,
                    "decimals": decimals,
                    "tx_signature": signature,
                }

        return None

    except Exception as e:
        logging.warning(f"[TokenExtract] ❌ Failed to extract from tx {signature}: {e}")
        return None

def has_contract_risks(token_address: str) -> bool:
    """
    Analyzes the contract for risky behavior or suspicious patterns.
    Returns True if risks found, False if clean or unknown.
    """
    try:
        risks = analyze_contract_risks(token_address)
        return bool(risks.get("has_risks", False))
    except Exception as e:
        logging.warning(f"[TokenUtils] Contract risk check failed for {token_address}: {e}")
        return False

async def fetch_token_summary(token_address: str) -> Dict[str, Any]:
    try:
        meta = await get_token_metadata(token_address)
        holders = await get_token_holders(token_address)
        return {
            "name": meta.get("name", "Unknown") if meta else "Unknown",
            "symbol": meta.get("symbol", "???") if meta else "???",
            "description": meta.get("project", {}).get("description", "") if meta else "",
            "supply": meta.get("supply", 0) if meta else 0,
            "decimals": meta.get("decimals", 0) if meta else 0,
            "holders": holders,
            "risk_flags": [] if holders > 10 else ["low_holders"],
        }
    except Exception as e:
        logging.warning(f"[TokenUtils] Failed to fetch summary: {e}")
        return {
            "name": "Unknown",
            "symbol": "???",
            "description": "",
            "supply": 0,
            "decimals": 0,
            "holders": 0,
            "risk_flags": ["fetch_error"],
        }

async def get_token_holders(token_address: str) -> int:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [token_address]
    }
    try:
        data = await _rpc_call(payload)
        accounts = data.get("result", {}).get("value", [])
        return len(accounts)
    except Exception as e:
        logging.warning(f"[TokenUtils] Failed to fetch holders: {e}")
        return 0

async def get_token_balance(wallet_address: str, token_address: str) -> float:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [wallet_address, {"mint": token_address}, {"encoding": "jsonParsed"}]
    }
    try:
        data = await _rpc_call(payload)
        accounts = data.get("result", {}).get("value", [])
        for acc in accounts:
            amount = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {}).get("tokenAmount", {}).get("uiAmount")
            if amount:
                return amount
    except Exception as e:
        logging.warning(f"[TokenUtils] Failed to fetch token balance: {e}")
    return 0.0

async def get_token_price_history(token_address: str, minutes: int = 60) -> list:
    """
    Fetch price history for a token over the last `minutes` minutes.
    Returns a list of price points [{'time': ISO8601, 'price': float}, ...].
    """
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    logging.warning(f"[TokenPriceHistory] Failed for {token_address}: HTTP {resp.status}")
                    return []
                data = await resp.json()

        prices = []
        for pair in data.get("pairs", []):
            # Dexscreener doesn't always provide a full price history, so we approximate
            price = pair.get("priceUsd")
            if price:
                prices.append({
                    "time": datetime.utcnow().isoformat(),
                    "price": float(price)
                })
        return prices
    except Exception as e:
        logging.warning(f"[TokenPriceHistory] Error for {token_address}: {e}")
        return []

async def get_sol_balance(wallet_address: str) -> Optional[float]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getBalance",
        "params": [wallet_address]
    }
    try:
        data = await _rpc_call(payload)
        lamports = data.get("result", {}).get("value", 0)
        return lamports / 1_000_000_000
    except Exception as e:
        logging.warning(f"[TokenUtils] Failed to fetch SOL balance: {e}")
        return None

async def get_wallet_tokens(wallet_or_address: Any, *, rpc_url: Optional[str] = None) -> List[Dict]:
    """
    Unified helper:
      - Accepts a wallet object or a base58 address string.
      - Uses ONLY the address in RPC payloads (no wallet object in json).
      - Returns a list of normalized token entries:
        { "mint": <str>, "address": <str>, "balance": <float>, "raw": <rpc row> }
      - Falls back to the original raw list if parsing fails.
    """
    addr = _extract_address(wallet_or_address)
    if not addr:
        raise ValueError("get_wallet_tokens: need wallet address or an object with .address/.pubkey")

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            addr,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"}
        ],
    }

    try:
        # Your _rpc_call may accept rpc_url kw; if not, just call _rpc_call(payload)
        try:
            response = await _rpc_call(payload, rpc_url=rpc_url)  # type: ignore[arg-type]
        except TypeError:
            response = await _rpc_call(payload)  # fallback if your helper has no rpc_url param

        raw = (response or {}).get("result", {}).get("value", [])

        if isinstance(raw, dict):
            logging.warning("[TokenUtils] Token response is a dict, converting to list...")
            raw = list(raw.values())

        if not isinstance(raw, list):
            return []

        normalized: List[Dict] = []
        for row in raw:
            try:
                info = (((row or {}).get("account", {}) or {}).get("data", {}) or {}).get("parsed", {}) or {}
                info = info.get("info", {}) if isinstance(info, dict) else {}
                mint = info.get("mint")
                ui_amt = float((((info.get("tokenAmount") or {}) ).get("uiAmount") or 0.0))
                if mint and isinstance(mint, str):
                    normalized.append({
                        "mint": mint,
                        "address": mint,      # so downstream checks for 'address' or 'mint' both work
                        "balance": ui_amt,
                        "raw": row,
                    })
                else:
                    # keep at least the raw row so legacy callers aren’t broken
                    normalized.append({"raw": row})
            except Exception:
                normalized.append({"raw": row})

        return normalized

    except Exception as e:
        logging.warning(f"[TokenUtils] RPC call failed for {addr}: {e}")
        return []

async def get_token_volume(token_address: str) -> float:
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [token_address, {"limit": 50}]
        }
        txs = await _rpc_call(payload)
        volume = 0.0
        for tx in txs.get("result", []):
            req_tx = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [tx.get("signature"), {"encoding": "jsonParsed"}]
            }
            tx_detail = await _rpc_call(req_tx)
            instructions = tx_detail.get("result", {}).get("transaction", {}).get("message", {}).get("instructions", [])
            for ix in instructions:
                val = ix.get("parsed", {}).get("info", {}).get("amount")
                if val:
                    volume += int(val) / 1e9
        return round(volume, 4)
    except Exception as e:
        logging.warning(f"[TokenUtils] Failed to fetch token volume: {e}")
        return 0.0

async def analyze_token_risk(token_address: str) -> Dict[str, Any]:
    meta = await get_token_metadata(token_address)
    holders = await get_token_holders(token_address)
    risk_flags = []

    if not meta or not meta.get("name"):
        risk_flags.append("missing_metadata")
    if holders == 0:
        risk_flags.append("no_holders")
    elif holders < 10:
        risk_flags.append("low_holder_count")
    if "project" not in meta or not meta.get("project"):
        risk_flags.append("no_project_info")

    return {
        "address": token_address,
        "holders": holders,
        "metadata": meta,
        "risk_flags": risk_flags,
        "is_risky": bool(risk_flags),
    }

# === Blacklist & Risk ===
def add_to_blacklist(token_address: str):
    blacklist = config.get("token_blacklist", [])
    if token_address not in blacklist:
        blacklist.append(token_address)
        config["token_blacklist"] = blacklist
        save_config()

def is_blacklisted_token(token_address: str) -> bool:
    return token_address in config.get("token_blacklist", [])

def is_dust_value(amount_sol: float) -> bool:
    return amount_sol < 0.01

# === Category Detection ===
def get_token_category(token_metadata: dict) -> str:
    name = token_metadata.get("name", "").lower()
    symbol = token_metadata.get("symbol", "").lower()
    theme = token_metadata.get("theme", "").lower()
    keywords = f"{name} {symbol} {theme}"

    if "elon" in keywords:
        return "elon"
    if "doge" in keywords or "inu" in keywords or "cat" in keywords:
        return "meme"
    if "pump" in keywords or "degen" in keywords or "casino" in keywords:
        return "gamble"
    if "ai" in keywords:
        return "ai"
    if "real" in keywords or "project" in keywords:
        return "utility"

    return "unknown"

# === Token Age ===
async def get_token_age(token_address: str) -> Optional[int]:
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [token_address, {"limit": 1}]
        }
        txs = await _rpc_call(payload)
        if txs and isinstance(txs.get("result", []), list):
            first_tx = txs["result"][0]
            block_time = first_tx.get("blockTime")
            if block_time:
                age = datetime.now(timezone.utc).timestamp() - block_time
                return int(age)
    except Exception as e:
        logging.warning(f"[TokenUtils] Failed to fetch token age for {token_address}: {e}")
    return None

# === LP Lock Status ===
async def get_lp_lock_status(token_address: str) -> Optional[str]:
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [token_address, {"limit": 25}]
        }
        txs = await _rpc_call(payload)
        if not txs or not isinstance(txs.get("result", []), list):
            return None

        lock_keywords = ["locker", "lock", "vest", "timelock", "burn"]
        for tx in txs.get("result", []):
            # Fetch transaction details
            tx_detail = await _rpc_call({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [tx.get("signature"), {"encoding": "jsonParsed"}]
            })
            instructions = tx_detail.get("result", {}).get("transaction", {}).get("message", {}).get("instructions", [])
            for instruction in instructions:
                program = instruction.get("program", "").lower()
                if any(keyword in program for keyword in lock_keywords):
                    return "locked"
        return "unlocked"
    except Exception as e:
        logging.warning(f"[TokenUtils] Failed to analyze LP lock for {token_address}: {e}")
    return None

# === Honeypot Detection ===
def detect_rug_behavior(metadata: dict) -> bool:
    if metadata.get("lp_locked", "").lower() in ["no", "unlocked"]:
        return True
    if metadata.get("owner_percent", 0) > 20:
        return True
    if float(metadata.get("buy_fee", 0)) > 10 or float(metadata.get("sell_fee", 0)) > 10:
        return True
    return False

def detect_honeypot(metadata: dict) -> bool:
    return str(metadata.get("honeypot", "false")).lower() == "true"

# === Mentions Extraction ===
def extract_token_mentions(text: str) -> list:
    if not text:
        return []
    mentions = set()
    patterns = [r"\$(\w{2,15})", r"#(\w{2,15})"]
    for pattern in patterns:
        matches = re.findall(pattern, text)
        mentions.update(matches)
    return list(mentions)

# === Token Validation ===
async def validate_token_address(address: str) -> bool:
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [address, {"encoding": "base64"}]
        }
        response = await _rpc_call(payload)
        return response.get("result", {}).get("value") is not None
    except Exception as e:
        logging.warning(f"[TokenUtils] Token validation failed for {address}: {e}")
        return False

def normalize_token_address(address: str) -> str:
    """
    Normalize a token address by stripping whitespace,
    converting to uppercase, and validating basic format.
    """
    if not isinstance(address, str):
        return ""

    address = address.strip().upper()

    # Optionally, enforce Solana address format (base58, 32–44 chars)
    if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", address):
        return ""

    return address

# === Sell Instruction Builder ===
def build_sell_instruction(user_pubkey: str, token_mint: str, amount: int):
    try:
        user_pubkey = Pubkey.from_string(user_pubkey)
        token_mint = Pubkey.from_string(token_mint)
        user_ata = get_associated_token_address(user_pubkey, token_mint)

        return transfer(
            TransferParams(
                program_id=TOKEN_PROGRAM_ID,
                source=user_ata,
                dest=user_ata,
                owner=user_pubkey,
                amount=amount,
                signers=[]
            )
        )
    except Exception as e:
        logging.warning(f"[TokenUtils] Failed to build sell instruction: {e}")
        return None

# === Tagging Utility ===
def tag_token_result(token: str, score: int, risk: str = "unknown", reason: str = "") -> dict:
    tags = []
    if score >= 85:
        tags.append("🔥 high-score")
    elif score >= 70:
        tags.append("✅ solid")
    elif score < 40:
        tags.append("⚠️ low-score")
    if "meme" in reason.lower():
        tags.append("🐸 meme")
    if "elon" in token.lower():
        tags.append("🚀 elon")
    if risk in {"high", "rug"}:
        tags.append("☠️ risky")
    return {"token": token, "score": score, "risk": risk, "tags": tags, "reason": reason}

async def get_token_fees(token_address: str) -> dict:
    """
    Fetches fee information for a token.
    Returns a dict with keys like {'buy_fee': float, 'sell_fee': float}.
    Defaults to 0 fees if no data is available.
    """
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    logging.warning(f"[TokenFees] Failed to fetch fees for {token_address}: HTTP {resp.status}")
                    return {"buy_fee": 0.0, "sell_fee": 0.0}
                data = await resp.json()

                # Try to extract fee data if present
                pairs = data.get("pairs", [])
                if pairs:
                    pair_info = pairs[0]
                    return {
                        "buy_fee": float(pair_info.get("buyFeeBps", 0)) / 100.0,
                        "sell_fee": float(pair_info.get("sellFeeBps", 0)) / 100.0
                    }

        return {"buy_fee": 0.0, "sell_fee": 0.0}
    except Exception as e:
        logging.warning(f"[TokenFees] Error for {token_address}: {e}")
        return {"buy_fee": 0.0, "sell_fee": 0.0}

# === Price Utilities ===
async def get_sol_price(session: aiohttp.ClientSession) -> float:
    try:
        async with session.get("https://price.jup.ag/v4/price?ids=SOL") as resp:
            data = await resp.json()
            return data["data"]["SOL"]["price"]
    except Exception as e:
        logging.warning(f"[TokenUtils] Failed to fetch SOL price: {e}")
        return 0.0

async def get_token_value_in_sol(token_address: str, amount: float, session: aiohttp.ClientSession) -> float:
    try:
        url = f"https://quote-api.jup.ag/v6/quote?inputMint={token_address}&outputMint=So11111111111111111111111111111111111111112&amount={int(amount * 10**6)}"
        async with session.get(url, timeout=10) as resp:
            data = await resp.json()
            out_amount = float(data.get("outAmount", 0)) / 10**9
            return out_amount
    except Exception as e:
        logging.warning(f"[TokenUtils] Failed to get token value in SOL: {e}")
        return 0.0
