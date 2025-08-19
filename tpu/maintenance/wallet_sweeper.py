import asyncio

from inputs.wallet.multi_wallet_manager import multi_wallet
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solana.system_program import TransferParams, transfer
from solana.transaction import Transaction
from utils.logger import log_event
from utils.rpc_loader import get_active_rpc, report_rpc_failure
from utils.service_status import update_status

LAMPORTS_PER_SOL = 1_000_000_000
SWEEP_THRESHOLD = 0.5     # Sweep if wallet has > 0.5 SOL
SWEEP_LIMIT = 5.0         # Max 5 SOL per sweep
SWEEP_INTERVAL = 21600    # Every 6 hours

async def run_wallet_sweeper():
    update_status("wallet_sweeper")
    while True:
        try:
            main_wallet = multi_wallet.get_main_wallet()
            if not main_wallet:
                log_event("⚠️ Cannot sweep — main wallet not found.")
                await asyncio.sleep(SWEEP_INTERVAL)
                continue

            rpc_url = get_active_rpc()

            for wallet in multi_wallet.wallets:
                if wallet.address == main_wallet.address:
                    continue  # Skip self

                balance = await wallet.get_balance(rpc_url)
                if balance <= SWEEP_THRESHOLD:
                    log_event(f"⏩ Skipping {wallet.address[:6]}... — balance too low ({balance:.3f} SOL)")
                    continue

                sweep_amount = min(balance - 0.2, SWEEP_LIMIT)
                if sweep_amount < 0.3:
                    log_event(f"⏩ Skipping {wallet.address[:6]}... — sweep amount too small ({sweep_amount:.2f} SOL)")
                    continue

                lamports = int(sweep_amount * LAMPORTS_PER_SOL)
                tx = Transaction().add(
                    transfer(
                        TransferParams(
                            from_pubkey=wallet.public_key,
                            to_pubkey=main_wallet.public_key,
                            lamports=lamports
                        )
                    )
                )

                async with AsyncClient(rpc_url) as client:
                    try:
                        bh = (await client.get_latest_blockhash())["result"]["value"]["blockhash"]
                        tx.recent_blockhash = bh
                        tx.sign(wallet.keypair)
                        sig = await client.send_transaction(tx, wallet.keypair, opts=TxOpts(skip_preflight=True))
                        log_event(f"🔁 Swept {sweep_amount:.2f} SOL from {wallet.address[:6]}... → main wallet | TX: {sig['result']}")
                    except Exception as e:
                        report_rpc_failure(rpc_url)
                        log_event(f"❌ Sweep failed for {wallet.address[:6]}... → main wallet: {e}")

        except Exception as e:
            log_event(f"⚠️ Wallet sweeping error: {e}")

        await asyncio.sleep(SWEEP_INTERVAL)
