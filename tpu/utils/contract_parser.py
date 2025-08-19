import json
import logging
from typing import Dict

import aiohttp


def parse_contract_data(contract_json: str) -> Dict:
    """
    Parses contract metadata or ABI JSON.
    """
    try:
        return json.loads(contract_json)
    except Exception as e:
        logging.warning(f"[ContractParser] Failed to parse contract JSON: {e}")
        return {}

def has_red_flags(abi: Dict) -> bool:
    """
    Very simple heuristic to detect potentially malicious functions.
    """
    suspicious = ["mint", "burn", "blacklist", "owner"]
    try:
        functions = json.dumps(abi).lower()
        return any(flag in functions for flag in suspicious)
    except Exception:
        return False


SOLSCAN_PROGRAM_API = "https://public-api.solscan.io/account/{}"
HEADERS = {
    "accept": "application/json",
    "User-Agent": "nyx-sniper"
}

async def analyze_contract_risks(token_address: str) -> dict:
    """
    Fetches and analyzes Solana program contract risks using Solscan.
    Looks for signs like upgradeability, lack of verification, suspicious ownership.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SOLSCAN_PROGRAM_API.format(token_address), headers=HEADERS) as resp:
                if resp.status != 200:
                    logging.warning(f"[ContractParser] Solscan lookup failed for {token_address} | Status: {resp.status}")
                    return {"has_risks": False, "details": []}
                
                data = await resp.json()

        # === Risk Heuristics ===
        risks = []

        # Not verified
        if not data.get("isProgramVerified", False):
            risks.append("Not verified program")

        # Upgrade authority present
        if data.get("upgradeAuthority"):
            risks.append("Upgradeable contract")

        # Suspicious deployer or ownership
        owner = data.get("ownerProgram")
        if owner and owner.lower() in ["11111111111111111111111111111111", "deadc0de..."]:
            risks.append("Suspicious owner program")

        return {
            "has_risks": len(risks) > 0,
            "details": risks,
        }

    except Exception as e:
        logging.warning(f"[ContractParser] Exception while analyzing contract {token_address}: {e}")
        return {"has_risks": False, "details": []}
