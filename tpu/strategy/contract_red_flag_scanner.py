import logging
import re

from utils.contract_parser import fetch_contract_code

logger = logging.getLogger("SniperBot")

RED_FLAG_PATTERNS = {
    "blacklist": r"(blacklist|black_list|addToBlacklist)",
    "whitelist": r"(whitelist|white_list|onlyWhitelisted)",
    "anti_bot": r"(cooldown|maxTxAmount|antiBot|earlyBuyerPenalty)",
    "owner_control": r"(setFees|changeTax|setMarketingWallet|renounceOwnership)",
    "stealth_mint": r"(mint|_mint|createTokens)",
    "honeypot": r"(canSell|_canSell|setSellLockTime)",
    "lock_bypass": r"(setUniswapV2Pair|disableLimits|openTrading)"
}

def scan_contract_risks(token_address: str) -> dict:
    code = fetch_contract_code(token_address)
    if not code:
        return {"score": 0, "flags": [], "details": ["No code found"]}

    flags = []
    for label, pattern in RED_FLAG_PATTERNS.items():
        if re.search(pattern, code, re.IGNORECASE):
            flags.append(label)

    score = -2 * len(flags)
    return {
        "score": score,
        "flags": flags,
        "details": [f"⚠️ Red flag: {f}" for f in flags] if flags else ["✅ No red flags"]
    }
