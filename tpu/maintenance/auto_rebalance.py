import asyncio

from core.live_config import config
from exec.trade_executor import TradeExecutor
from inputs.wallet.multi_wallet_manager import multi_wallet
from strategy.strategy_memory import get_highest_scoring_idle_token
from utils.logger import log_event
from utils.rpc_loader import get_active_rpc, report_rpc_failure
from utils.service_status import update_status
from utils.token_utils import get_sol_balance

REBALANCE_INTERVAL = 86400  # 24 hours
REBALANCE_DESTINATION = "7jyLcpovGRS24XYgmDbM87A9faSHqchPamNZvwa8jQFJ"
REBALANCE_MINIMUM = 20.0
REBALANCE_THRESHOLD = 0.5
REBALANCE_CAP = 10.0


async def rebalance_single_wallet(wallet, telegram_interface=None):
    try:
        rpc_http = get_active_rpc()
        sol_balance = await get_sol_balance(wallet.address)

        # === Cold wallet rebalance ===
        if sol_balance > REBALANCE_MINIMUM + REBALANCE_THRESHOLD:
            excess = sol_balance - REBALANCE_MINIMUM
            amount_to_send = min(excess, REBALANCE_CAP)

            result = await wallet.transfer_sol(REBALANCE_DESTINATION, amount_to_send)
            if result:
                log_event(f"💸 {wallet.address[:6]}..: Rebalanced {amount_to_send:.2f} SOL to cold wallet.")
                if telegram_interface and hasattr(telegram_interface, "send_message"):
                    await telegram_interface.send_message(
                        f"💸 *{wallet.address[:6]}..*: Rebalanced {amount_to_send:.2f} SOL.\n"
                        f"Balance retained: {REBALANCE_MINIMUM} SOL"
                    )
            else:
                log_event(f"⚠️ {wallet.address[:6]}..: Rebalance failed.")

        # === AI Rebuy Logic ===
        else:
            blacklist = set(config.get("token_blacklist", []))
            result = get_highest_scoring_idle_token(blacklist)

            if result:
                token_address, _, confidence = result
                buy_amount = min(confidence / 100, 1.0)

                try:
                    executor = TradeExecutor(wallet)
                    tx = await executor.buy_token(token_address, buy_amount)

                    if tx:
                        log_event(f"🔁 {wallet.address[:6]}..: Rebuy {token_address} for {buy_amount:.2f} SOL")
                        if telegram_interface and hasattr(telegram_interface, "send_message"):
                            await telegram_interface.send_message(
                                f"🔁 *{wallet.address[:6]}..*: Rebuy {token_address} for {buy_amount:.2f} SOL"
                            )
                except Exception as e:
                    log_event(f"⚠️ Failed to execute AI rebuy: {e}")

    except Exception as e:
        report_rpc_failure(get_active_rpc())
        log_event(f"❌ Error rebalancing {wallet.address[:6]}..: {e}")


async def rebalance_all_wallets(telegram_interface=None):
    update_status("manual_wallet_rebalance")
    try:
        wallets = multi_wallet.load_all_wallets() or []
        if not wallets:
            log_event("⚠️ No wallets available for manual rebalance.")
            return

        tasks = [rebalance_single_wallet(wallet, telegram_interface) for wallet in wallets]
        await asyncio.gather(*tasks)

    except Exception as e:
        log_event(f"❌ Manual rebalance failed: {e}")


async def run_auto_rebalance(_, telegram_interface=None):
    update_status("auto_rebalance")
    try:
        while True:
            wallets = multi_wallet.load_all_wallets() or []
            if not wallets:
                log_event("⚠️ No wallets available for auto rebalance.")
            else:
                tasks = []
                for wallet in wallets:
                    sol_balance = await get_sol_balance(wallet["address"])
                    if sol_balance:  # Only rebalance if something is held
                        tasks.append(rebalance_single_wallet(wallet, telegram_interface))
                if not tasks:
                    log_event("⚠️ No wallets have active tokens for auto rebalance.")
                else:
                    await asyncio.gather(*tasks)

            await asyncio.sleep(REBALANCE_INTERVAL)

    except Exception as e:
        log_event(f"❌ Auto-rebalance master error: {e}")
