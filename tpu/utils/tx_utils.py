import asyncio
import base64
import logging
from typing import List, Optional, Union

from core.live_config import config
from librarian.data_librarian import librarian
from solana.rpc.api import Client
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed, Finalized
from solana.rpc.types import TxOpts
from solana.transaction import Transaction
from utils.logger import log_event
from utils.rpc_loader import get_active_rpc

# Optional – only if you have CU + priority fee helpers available
try:
    from spl.token.instructions import get_associated_token_address  # noqa
except Exception:
    pass

# === Defaults (fallbacks to live_config if present) ===
DEFAULT_CONFIRM_TIMEOUT = 20   # seconds
DEFAULT_CONFIRM_RETRIES = 10   # attempts
DEFAULT_PRIORITY_LAMPORTS = 0  # override in config["priority_fee_lamports"]
DEFAULT_CU_LIMIT = 1_000_000   # override in config["compute_unit_limit"]

# We build sync + async clients on-demand using the active RPC
def _sync_client() -> Client:
    return Client(get_active_rpc())

def _async_client() -> AsyncClient:
    return AsyncClient(get_active_rpc(), timeout=30)


# ---------------------------------------------------------------------
# Building / Simulating
# ---------------------------------------------------------------------
def simulate_transaction(client: Client, instructions: List, signer, boost: bool = False) -> dict:
    """
    Simulate a transaction composed of the given instructions (SYNC).
    Returns the simulation result dictionary.
    """
    try:
        tx = Transaction()
        if boost:
            try:
                from solders.compute_budget import (  # type: ignore
                    set_compute_unit_limit,
                    set_compute_unit_price,
                )
                cu_ix = set_compute_unit_limit(config.get("compute_unit_limit", DEFAULT_CU_LIMIT))
                pr_ix = set_compute_unit_price(config.get("priority_fee_lamports", DEFAULT_PRIORITY_LAMPORTS))
                tx.add(cu_ix, pr_ix)
            except Exception:
                pass

        for ix in instructions:
            tx.add(ix)

        recent = client.get_latest_blockhash()["result"]["value"]["blockhash"]
        tx.recent_blockhash = recent
        tx.sign(signer)
        result = client.simulate_transaction(tx, sig_verify=False, commitment=Confirmed)
        return result.get("result", {})
    except Exception as e:
        logging.error(f"[tx_utils] ❌ Sim TX error: {e}")
        return {"err": str(e)}


async def build_swap_instruction(payer, token_address: str, amount_sol: float, rpc_url: Optional[str] = None):
    """
    Thin wrapper that asks the AMM router for a swap instruction.
    Implemented here so TradeExecutor can import a single place.
    """
    try:
        # You should have a router that knows how to build Raydium/Orca swap ix:
        # e.g. modules.amm_router.build_swap_ix(...)
        from exec.amm_router import build_swap_ix  # your central AMM builder
        return await build_swap_ix(payer, token_address, amount_sol, rpc_url or get_active_rpc())
    except ImportError:
        # Fallback to your previous Raydium/Orca splitter
        try:
            from utils.raydium_orca_router import build_swap_ix as build_swap_ix_alt
            return await build_swap_ix_alt(payer, token_address, amount_sol, rpc_url or get_active_rpc())
        except Exception as e:
            logging.warning(f"[tx_utils] No AMM router available to build swap ix: {e}")
            return None
    except Exception as e:
        logging.warning(f"[tx_utils] build_swap_instruction failed: {e}")
        return None


# ---------------------------------------------------------------------
# Send / Confirm
# ---------------------------------------------------------------------
async def sign_and_send_tx(raw_tx_or_tx: Union[str, Transaction], wallet=None) -> Optional[str]:
    """
    Accepts either:
      - base64-encoded raw transaction string
      - a Transaction object (will sign with provided 'wallet')
    Returns signature if broadcasted.
    """
    try:
        rpc = get_active_rpc()
        async with _async_client() as client:
            if isinstance(raw_tx_or_tx, Transaction):
                if wallet is None:
                    raise ValueError("Wallet required to sign Transaction object")
                # Add compute/priority (optional)
                _maybe_inject_priority(raw_tx_or_tx)
                bh = await client.get_latest_blockhash()
                raw_tx_or_tx.recent_blockhash = bh.value.blockhash
                raw_tx_or_tx.sign(wallet.keypair)
                raw_bytes = raw_tx_or_tx.serialize()
                b64 = base64.b64encode(raw_bytes).decode("utf-8")
            else:
                # Already base64-encoded
                b64 = raw_tx_or_tx

            opts = TxOpts(skip_preflight=config.get("skip_preflight", True))
            resp = await client.send_raw_transaction(b64, opts=opts)
            sig = resp.value if hasattr(resp, "value") else resp.get("result")
            if not sig:
                logging.warning(f"[tx_utils] No signature returned: {resp}")
                return None

            # Confirm it (optional)
            finalized = await confirm_transaction(client, sig, use_finalized=True)
            if finalized:
                log_event(f"✅ TX confirmed: {sig}")
            else:
                logging.warning(f"⚠️ TX not confirmed in time: {sig}")
            return sig
    except Exception as e:
        logging.error(f"[tx_utils] ❌ sign_and_send_tx failed: {e}")
        return None


async def send_raw_tx(b64_tx: str) -> Optional[str]:
    """
    Sends a raw base64 tx string and returns the signature.
    """
    try:
        async with _async_client() as client:
            opts = TxOpts(skip_preflight=config.get("skip_preflight", True))
            resp = await client.send_raw_transaction(b64_tx, opts=opts)
            sig = resp.value if hasattr(resp, "value") else resp.get("result")
            return sig
    except Exception as e:
        logging.error(f"[tx_utils] ❌ send_raw_tx failed: {e}")
        return None


def _maybe_inject_priority(tx: Transaction):
    """
    Optionally inject compute unit & priority fee instructions.
    Uses solders.compute_budget if available.
    """
    try:
        from solders.compute_budget import (  # type: ignore
            set_compute_unit_limit,
            set_compute_unit_price,
        )
        cu = config.get("compute_unit_limit", DEFAULT_CU_LIMIT)
        pr = config.get("priority_fee_lamports", DEFAULT_PRIORITY_LAMPORTS)
        if cu:
            tx.add(set_compute_unit_limit(int(cu)))
        if pr and pr > 0:
            tx.add(set_compute_unit_price(int(pr)))
    except Exception:
        # silently ignore if not available
        pass


# ---------------------------------------------------------------------
# Confirmation helpers
# ---------------------------------------------------------------------
async def confirm_token_sell(client: AsyncClient, tx_signature: str) -> bool:
    """
    Legacy: confirms whether a token sell transaction was successfully finalized on-chain.
    """
    try:
        retries = config.get("confirm_retries", DEFAULT_CONFIRM_RETRIES)
        timeout = config.get("confirm_timeout", DEFAULT_CONFIRM_TIMEOUT)
        for attempt in range(retries):
            logging.debug(f"🔍 [confirm_token_sell] Checking TX: {tx_signature} (attempt {attempt + 1})")
            result = await client.get_confirmed_transaction(tx_signature)
            if result.value:
                logging.info(f"✅ Token sell confirmed: {tx_signature}")
                log_event("Confirmed sell TX", tx_signature)
                _log_ai_conf(tx_signature, True)
                return True
            await asyncio.sleep(timeout / retries)
        logging.warning(f"⚠️ Token sell NOT confirmed after {retries} attempts: {tx_signature}")
        _log_ai_conf(tx_signature, False)
        return False
    except Exception as e:
        logging.error(f"❌ Error confirming token sell TX {tx_signature}: {e}")
        return False


async def confirm_transaction_finalized(client: AsyncClient, tx_signature: str) -> bool:
    """
    Confirms a transaction has reached 'finalized' status.
    """
    try:
        retries = config.get("confirm_retries", DEFAULT_CONFIRM_RETRIES)
        timeout = config.get("confirm_timeout", DEFAULT_CONFIRM_TIMEOUT)
        for attempt in range(retries):
            response = await client.get_signature_statuses([tx_signature])
            status = response.value[0] if response.value else None
            if status and status.confirmation_status == "finalized":
                logging.info(f"✅ TX finalized: {tx_signature}")
                log_event("Finalized TX", tx_signature)
                _log_ai_conf(tx_signature, True)
                return True
            await asyncio.sleep(timeout / retries)
        logging.warning(f"⏳ TX not finalized after {retries} checks: {tx_signature}")
        _log_ai_conf(tx_signature, False)
        return False
    except Exception as e:
        logging.error(f"❌ Error checking finalization for TX {tx_signature}: {e}")
        return False


async def confirm_transaction(client: AsyncClient, tx_signature: str, use_finalized: bool = True) -> bool:
    """
    Unified transaction confirmation method.
    """
    if use_finalized:
        return await confirm_transaction_finalized(client, tx_signature)
    else:
        return await confirm_token_sell(client, tx_signature)


# ---------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------
def _log_ai_conf(tx_signature: str, ok: bool):
    try:
        entry = {
            "tx": tx_signature,
            "confirmed": ok,
            "timestamp": datetime.utcnow().isoformat()
        }
        librarian.append_log("tx_confirmations", entry)
    except Exception:
        pass
