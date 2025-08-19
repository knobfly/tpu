# onchain_listener.py

import asyncio
import json
import logging

import websockets
from core.live_config import config
from exec.auto_sell_logic import evaluate_early_exit
from inputs.onchain.firehose.firehose_watchdog import check_firehose_health
from strategy.stop_snipe_defender import is_stop_snipe_mode, register_rug_event
from utils.honeypot_detector import check_if_honeypot
from utils.logger import log_event
from utils.price_fetcher import get_token_price
from utils.service_status import update_status
from cortex.core_router import handle_event

from utils.token_utils import (
    get_token_metadata,
    is_blacklisted_token,
    is_dust_value,
    validate_token_address,
)
from utils.websocket_loader import get_active_ws

RAYDIUM_PROGRAMS = [
    "RVKd61ztZW9mq8wCnD1iDfbTtyjcibGZ2y3sPzoiUJq",
    "9tdctL2kJHREvJBCXJhfpapM7pWxEt49RdcMGsTCTQnD"
]
ORCA_PROGRAMS = [
    "82yxjeMs7fA3C5exzL84uCPpGEECJpLfByZYkzqUvvjD",
    "6UeJFcVRZ5bN2W2kE3KYuyUTqz9KtZctUPBhYfczEeqF"
]


class OnchainListener:
    def __init__(self, wallet, telegram, trade_executor, auto_sell, logger, feeding_frenzy):
        self.wallet = wallet
        self.telegram = telegram
        self.trade_executor = trade_executor
        self.auto_sell = auto_sell
        self.logger = logger
        self.feeding_frenzy = feeding_frenzy
        self.active_mints = set()
        self.frenzy_count = 0

    async def start(self):
        asyncio.create_task(self._heartbeat())

        while True:
            #if check_firehose_health():
            #   await asyncio.sleep(5)
            #    continue  # firehose healthy, skip WS fallback

            try:
                ws_url = get_active_ws()
                if not ws_url:
                    log_event("⚠️ No fallback WS available.")
                    await asyncio.sleep(5)
                    continue

                async with websockets.connect(ws_url) as ws:
                    for program_id in RAYDIUM_PROGRAMS + ORCA_PROGRAMS:
                        sub = {
                            "jsonrpc": "2.0",
                            "id": program_id,
                            "method": "logsSubscribe",
                            "params": [
                                {"mentions": [program_id]},
                                {"commitment": "finalized"}
                            ]
                        }
                        await ws.send(json.dumps(sub))

                    log_event("🛟 OnchainListener fallback WS active.")

                    while True:
                        try:
                            msg = await ws.recv()
                            data = json.loads(msg)
                            if isinstance(data, dict) and "params" in data:
                                await self._handle_message(data["params"])
                        except Exception as inner_e:
                            log_event(f"[OnchainListener] WS error: {inner_e}")
                            break

            except Exception as e:
                log_event(f"[OnchainListener] Connection failed: {e}")
                await asyncio.sleep(5)

    async def _heartbeat(self):
        while True:
            update_status("onchain_listener")
            await asyncio.sleep(30)

    async def _handle_message(self, msg: dict):
        try:
            logs = msg["result"]["value"].get("logs", [])
            tx_accounts = msg["result"]["value"].get("accounts", [])
            tx_sig = msg["result"]["value"].get("signature", "")

            for log_line in logs:
                if any(kw in log_line for kw in ["initialize", "Create", "swap", "add_liquidity"]):
                    for acc in tx_accounts:
                        if acc.startswith("So111") or acc in self.active_mints:
                            continue
                        await self._handle_detected_token(acc, tx_sig)
        except Exception as e:
            log_event(f"[OnchainListener] Failed to parse WS msg: {e}")

    async def _handle_detected_token(self, token_mint: str, tx_sig: str):
        try:
            if token_mint in self.active_mints:
                return
            if is_blacklisted_token(token_mint, set(config.get("token_blacklist", []))):
                return
            if len(self.active_mints) >= config.get("max_active_tokens", 10):
                return
            if is_stop_snipe_mode():
                return

            price = await get_token_price(token_mint)
            if price is None or is_dust_value(price):
                return
            if price < config.get("volume_threshold", 0):
                return
            if await check_if_honeypot(token_mint, self.wallet.keypair, config):
                register_rug_event()
                return

            token_metadata = await get_token_metadata(token_mint)
            from scoring.scoring_engine import score_token
            score_data = score_token(token_metadata)
            if score_data["score"] <= 0:
                return

            self.active_mints.add(token_mint)
            log_event(f"📡 OnchainListener detected token: {token_mint} | Score: {score_data['score']}")

            try:
                from strategy.strategy_memory import record_result
                record_result({
                    "token": token_mint,
                    "score": score_data["score"],
                    "source": "onchain_listener",
                    "metadata": token_metadata
                })
                await handle_event({
                    "token": mint,
                    "action": "txn_update",
                    "tx": tx_summary,            # {type:'swap', vol:..., liq:..., ts:...}
                    "wallets": involved_wallets, # optional list of wallet addresses
                    "source": "onchain_listener",
                })
            except Exception as e:
                log_event(f"[OnchainListener] Strategy memory skip: {e}")

            if self.feeding_frenzy.is_active():
                self.frenzy_count += 1
                asyncio.create_task(self._frenzy_sell_timer(token_mint))
            else:
                asyncio.create_task(self._standard_buy_flow(token_mint))

        except Exception as e:
            log_event(f"[OnchainListener] Token handling error: {e}")

    async def _standard_buy_flow(self, mint: str):
        try:
            tx = await self.trade_executor.buy_token(
                mint,
                config.get("buy_amount", 0.1),
                wallet=self.wallet
            )
            if tx and self.telegram:
                try:
                    from core.telegram_interface import send_telegram_alert
                    await send_telegram_alert(
                        config["telegram_token"],
                        config["telegram_chat_id"],
                        f"✅ Bought (fallback): {mint}\nTX: {tx}"
                    )
                except Exception as e:
                    log_event(f"[OnchainListener] Telegram alert failed: {e}")
            await asyncio.sleep(3)
            asyncio.create_task(self.auto_sell.monitor(mint))

        except Exception as e:
            log_event(f"[OnchainListener] Buy error: {e}")

    async def _frenzy_sell_timer(self, mint: str):
        try:
            tx = await self.trade_executor.buy_token(mint, 0.1, wallet=self.wallet)
            if tx:
                await asyncio.sleep(2)
                for _ in range(10):
                    if await evaluate_early_exit(mint, self.wallet, config):
                        await self.trade_executor.sell_token(mint, wallet=self.wallet)
                        return
                    await asyncio.sleep(1)
                await self.trade_executor.sell_token(mint, wallet=self.wallet)
        except Exception as e:
            log_event(f"[OnchainListener] Frenzy sell error: {e}")


# 🔁 External fallback callable
async def get_recent_transactions(token: str, limit: int = 20) -> list:
    import aiohttp
    from utils.rpc_loader import get_active_rpc

    try:
        rpc_url = get_active_rpc()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [token, {"limit": limit}]
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(rpc_url, json=payload, timeout=10) as resp:
                data = await resp.json()
                return data.get("result", [])
    except Exception as e:
        log_event(f"❌ get_recent_transactions error: {e}")
        return []
