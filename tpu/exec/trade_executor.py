# exec/trade_executor.py

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from core.live_config import config as global_config
from core.llm.llm_brain import init_llm_brain
from defense.race_protection import check_sandwich_risk
from defense.risk_aggression import get_risk_adjusted_amount
from exec.open_position_tracker import open_position_tracker
from inputs.meta_data.meta_strategy_engine import meta_strategy_engine  # kept for compatibility
from inputs.wallet.multi_wallet_manager import multi_wallet
from inputs.wallet.wallet_auto_rotator import wallet_auto_rotator
from runtime.event_bus import TradeOutcomeEvent, event_bus, now
from scoring.scoring_engine import score_token
from special.ai_self_tuner import tune_strategy
from special.insight_logger import log_trade_insight
from utils.amm_pools import build_slippage_aware_swap_ix
from utils.http_client import SafeSession
from utils.jupiter_swap import JupiterSwap
from utils.logger import log_event
from utils.price_fetcher import get_token_price
from utils.rpc_loader import get_active_rpc
from utils.service_status import update_status
from utils.telegram_utils import send_telegram_message
from utils.token_utils import add_to_blacklist, get_token_category, is_dust_value
from utils.tx_utils import sign_and_send_tx
from cortex.core_router import handle_event

# 🔌 stream controls (dynamic subs) — safe fallbacks if not present
try:
    from inputs.onchain.solana_stream_listener import (
        request_account_watch,
        request_signature_watch,
        stop_account_watch,
    )
except Exception:
    async def request_account_watch(*args, **kwargs): pass
    async def stop_account_watch(*args, **kwargs): pass
    async def request_signature_watch(*args, **kwargs): pass

# optional: ATA helper
try:
    from utils.wallet_utils import derive_associated_token_account
except Exception:
    def derive_associated_token_account(*args, **kwargs): return None


llm_brain = init_llm_brain()


class TradeExecutor:
    def __init__(self, wallet_manager=None, config=None, brain=None):
        from inputs.wallet.multi_wallet_manager import multi_wallet as mw
        self.config = config or global_config
        self.brain = brain
        # if a specific wallet manager is injected, use only that; otherwise load all
        self.wallets = mw.wallets if wallet_manager is None else [wallet_manager]

        # Lazily create the HTTP session & Jupiter instances (avoids "no running event loop")
        self._safe_factory = SafeSession
        self._safe = None
        self.session = None
        self.swap_instances: Dict[str, JupiterSwap] = {}

        # Subscribe to stream safety alerts (best-effort)
        try:
            async def _on_stream(evt: Dict[str, Any]):
                await self.handle_stream_alert(evt)
            event_bus().subscribe("lp_unlock", _on_stream)
            event_bus().subscribe("vault_drain", _on_stream)
            event_bus().subscribe("honeypot_detected", _on_stream)
        except Exception:
            pass

    # ---------- infra helpers ----------

    async def _get_session(self):
        if self.session and not getattr(self.session, "closed", True):
            return self.session

        # instantiate SafeSession only once you’re inside an event loop
        if self._safe is None:
            self._safe = self._safe_factory()

        if hasattr(self._safe, "ensure"):
            self.session = await self._safe.ensure()
        else:
            self.session = getattr(self._safe, "session", None)
            if not self.session:
                from aiohttp import ClientSession, ClientTimeout, TCPConnector
                self.session = ClientSession(
                    connector=TCPConnector(limit=100, enable_cleanup_closed=True),
                    timeout=ClientTimeout(total=15),
                )
        return self.session

    async def _get_swap(self, wallet):
        """Get or create a JupiterSwap tied to the given wallet."""
        sess = await self._get_session()
        swap = self.swap_instances.get(wallet.address)
        if not swap:
            swap = JupiterSwap(sess, wallet, logger=logging)
            self.swap_instances[wallet.address] = swap
        return swap

    # ---------- stream safety reactions ----------

    async def handle_stream_alert(self, evt: Dict[str, Any]):
        """
        React to real-time critical conditions: LP unlock, vault drain, honeypot.
        Exits any held position in the affected token.
        """
        try:
            et = (evt or {}).get("type")
            token = (evt or {}).get("token")
            if not token or not et:
                return

            # find a wallet that holds this token
            holder = None
            for w in self.wallets:
                try:
                    bal = await w.get_token_balance(token)
                    if bal and float(bal) > 0:
                        holder = (w, float(bal))
                        break
                except Exception:
                    continue
            if not holder:
                return

            w, bal = holder
            reason = None

            if et == "honeypot_detected":
                reason = "honeypot_stream_alert"
            elif et == "lp_unlock":
                reason = "lp_unlock_stream_alert"
            elif et == "vault_drain":
                sev = (evt.get("severity") or "low").lower()
                if sev in ("medium", "high"):
                    reason = f"vault_drain_{sev}"
                else:
                    # low severity: ignore for now
                    return
            else:
                return

            # exit position (full)
            await self.sell_token(token_address=token, amount_tokens=bal, profit_percent=None, wallet=w)
            log_event(f"🚨 Stream exit {token} ({reason})")

        except Exception as e:
            logging.warning(f"[TradeExecutor] handle_stream_alert failed: {e}")

    # ---------- buy flow ----------

    async def buy_token(
        self,
        token_address: str,
        base_amount: float,
        override_filters: bool = False,
        scanner_source: str = "manual",
    ) -> Optional[str]:
        update_status("trade_executor")
        try:
            # Scoring context; router will decide snipe|trade
            ctx = {
                "token_address": token_address,
                "mode": "snipe" if scanner_source in ("firehose", "snipe_trigger") else "trade",
                "scanner_source": scanner_source,
                "base_amount": base_amount,
            }
            await handle_event({
                "token": mint,
                "action": "trade_update",
                "meta": {"side": "buy", "size": size, "price": fill_price, "txid": txid},
                "source": "trade_executor",
            })
            result = score_token(ctx)  # sync ok; router handles loop blending internally
            if not result or "final_score" not in result:
                log_event(f"❌ Buy skipped: Failed to score {token_address}")
                return None

            score = float(result["final_score"])
            breakdown = result.get("breakdown", {})

            if score == 0 and not override_filters:
                log_event(f"❌ Buy skipped: Score too low ({score}) for {token_address}")
                return None

            if breakdown.get("honeypot_similarity", 0) < -10 and not override_filters:
                log_event(f"⚠️ Honeypot warning for {token_address} — skipping")
                return None

            if not override_filters:
                try:
                    high_risk = await check_sandwich_risk(token_address)
                    if high_risk:
                        log_event(f"🚨 High congestion for {token_address} — sandwich risk.")
                        return None
                except Exception:
                    pass  # best-effort; don't hard-fail the trade

            # choose wallet by strategy
            strategy = self.config.get("default_strategy", "trailing_stop")
            wallet = await wallet_auto_rotator.choose_wallet_for_strategy(
                strategy=strategy, min_threshold=0.4
            )
            if not wallet:
                log_event("❌ No wallet eligible via WalletAutoRotator")
                return None

            balance = await wallet.get_balance()
            if balance < 0.01:
                log_event(f"⚠️ Wallet {wallet.address} balance too low.")
                return None

            # sizing: 0–100 score → 0.2x..1.2x multiplier (configurable)
            mul_lo = float(self.config.get("size_mult_min", 0.2))
            mul_hi = float(self.config.get("size_mult_max", 1.2))
            scale = mul_lo + (mul_hi - mul_lo) * max(0.0, min(score, 100.0)) / 100.0

            # risk-adjusted base, then scale by score
            raw_amount = await get_risk_adjusted_amount(token_address, base_amount, score)
            target = raw_amount * scale

            # portfolio caps
            max_wallet_pct = float(self.config.get("max_wallet_risk_pct", 0.5))  # 50% wallet cap
            min_notional = float(self.config.get("min_notional_sol", 0.01))
            max_notional = float(self.config.get("max_notional_sol", 3.0))
            buy_amount = max(min(target, balance * max_wallet_pct), min_notional)
            buy_amount = min(buy_amount, max_notional)

            if buy_amount < min_notional:
                log_event(f"⚠️ Buy amount too low for {token_address}.")
                return None

            # Try direct AMM first for speed (if allowed)
            fast_snipe_enabled = self.config.get("fast_snipe_mode", True)
            try_amm_first = fast_snipe_enabled or scanner_source in ("firehose", "snipe_trigger")

            if try_amm_first:
                tx_sig = await self._direct_amm_buy(token_address, buy_amount, wallet)
                if tx_sig:
                    await self._after_successful_buy(
                        token_address, buy_amount, score, tx_sig, scanner_source, wallet, result
                    )
                    return tx_sig
                else:
                    log_event(f"⚠️ AMM swap path unavailable for {token_address}, fallback to Jupiter")

            swap = await self._get_swap(wallet)

            tx_sig = await self._smart_buy(
                swap=swap,
                token_address=token_address,
                buy_amount=buy_amount,
                try_direct_amm_first=try_amm_first,
                wallet=wallet,
            )
            if not tx_sig:
                return None

            # finalize bookkeeping for the smart path, too
            await self._after_successful_buy(
                token_address, buy_amount, score, tx_sig, scanner_source, wallet, result
            )
            return tx_sig

        except Exception as e:
            logging.exception(f"❌ Buy failed for {token_address}: {e}")
            return None

    async def _direct_amm_buy(self, token_address: str, amount_sol: float, wallet) -> Optional[str]:
        """Fast-path AMM buy using our slippage-aware instruction builder."""
        try:
            ix, meta = await build_slippage_aware_swap_ix(
                payer=wallet,
                token_mint=token_address,
                amount_in_sol=amount_sol,
                dex_hint=None,  # or "raydium"/"orca" if you want to force
                slippage_bps=self.config.get("swap_slippage_bps", 50),
            )
            if not ix:
                return None

            from solana.transaction import Transaction
            tx = Transaction()
            tx.add(ix)
            sig = await sign_and_send_tx(tx, wallet=wallet)
            if sig:
                log_event(f"🚀 Direct AMM snipe executed on {meta['dex']} (min_out={meta['min_out']}), sig={sig}")
            return sig
        except Exception as e:
            logging.warning(f"[TradeExecutor] Direct AMM buy failed: {e}")
            return None

    async def _smart_buy(
        self,
        swap: JupiterSwap,
        token_address: str,
        buy_amount: float,
        try_direct_amm_first: bool,
        wallet,
    ) -> Optional[str]:
        """
        Place a buy, optionally split across 2–3 tranches if impact is high or order is large.
        Returns tx_sig or None.
        """
        max_price_impact = float(self.config.get("max_price_impact_pct", 0.12))  # 12%
        split_enabled = bool(self.config.get("split_order_enabled", True))
        big_order_threshold = float(self.config.get("split_order_threshold_sol", 1.0))  # split if >= 1 SOL

        # Impact probe (if supported)
        high_impact = False
        try:
            if hasattr(swap, "quote_buy"):
                q = await swap.quote_buy(token_address, buy_amount)
                if q and float(q.get("priceImpactPct", 0)) > max_price_impact:
                    high_impact = True
        except Exception:
            pass

        # Decide split plan
        do_split = split_enabled and (high_impact or buy_amount >= big_order_threshold)
        if not do_split:
            if try_direct_amm_first:
                tx = await self._direct_amm_buy(token_address, buy_amount, wallet)
                if tx:
                    return tx
            return await swap.buy_token(token_address, buy_amount)

        # Split into 2–3 legs (60/40 or 40/30/30) with tiny pauses
        if buy_amount >= big_order_threshold * 2:
            legs = [buy_amount * 0.4, buy_amount * 0.3, buy_amount * 0.3]
        else:
            legs = [buy_amount * 0.6, buy_amount * 0.4]

        tx_sig = None
        for i, part in enumerate(legs):
            if try_direct_amm_first and i == 0:
                tx_sig = await self._direct_amm_buy(token_address, part, wallet)
                if not tx_sig:
                    tx_sig = await swap.buy_token(token_address, part)
            else:
                tx_sig = await swap.buy_token(token_address, part)

            if not tx_sig:
                # stop if a leg fails
                break
            await asyncio.sleep(float(self.config.get("split_order_pause_s", 1.2)))

        return tx_sig

    async def _after_successful_buy(
        self,
        token_address: str,
        buy_amount: float,
        score: float,
        tx_sig: str,
        scanner_source: str,
        wallet,
        result: dict,
    ):
        """Finalize bookkeeping, insights, learning, and wire dynamic stream watchers."""
        # 1) Price & category (best-effort)
        category = None
        try:
            category = await get_token_category(token_address)
        except Exception:
            pass

        price = None
        try:
            price = await get_token_price(token_address)
        except Exception:
            pass

        if price is None or is_dust_value(price):
            log_event(f"💨 Token {token_address} flagged as dust. Auto-blacklisted.")
            add_to_blacklist(token_address)

        # 2) Persist trade + insights
        self._record_trade("buy", token_address, buy_amount, price, tx_sig, scanner_source, category, wallet.address)
        log_trade_insight(token_address, "buy", score, success=True, tx=tx_sig)

        # 3) Track open position
        try:
            open_position_tracker.add_position(
                wallet=wallet.address,
                token=token_address,
                amount=buy_amount,
                price=price or 0.0,
                strategy_id=self.config.get("default_strategy", "trailing_stop"),
                token_symbol=result.get("symbol", token_address),
            )
        except Exception as e:
            logging.warning(f"[TradeExecutor] Failed to update open_position_tracker: {e}")

        # 4) PnL scaffold (0 on entry)
        try:
            await multi_wallet.record_trade_pnl(wallet=wallet, pnl_sol=0, was_profit=None, tx_sig=tx_sig, token=token_address)
        except Exception as e:
            logging.warning(f"[TradeExecutor] Failed to log buy to multi_wallet: {e}")

        # 5) Strategy feedback (tuner)
        try:
            await tune_strategy({
                "token_name": result.get("symbol", token_address),
                "token_address": token_address,
                "final_score": score,
                "outcome": "buy",
                "pnl_pct": 0.0,
            })
        except Exception as e:
            logging.warning(f"[TradeExecutor] Failed to send buy feedback to tuner: {e}")

        # 6) LLM reflection (best-effort)
        try:
            reflection = await llm_brain.reflect({
                "token": token_address,
                "action": "buy",
                "score": score,
                "wallet": wallet.address,
                "price": price or 0.0,
            })
            
            if reflection:
                await send_telegram_message(f"🧠 *Post-Trade Reflection:*\n{reflection}")
        except Exception as e:
            logging.warning(f"[TradeExecutor] Reflection failed: {e}")

        # 7) 🔴 Stream watchers (dynamic)
        try:
            if tx_sig:
                await request_signature_watch(tx_sig)
        except Exception as e:
            logging.warning(f"[TradeExecutor] signature watch failed: {e}")

        await self._watch_pool_and_ata(token_address, wallet.address)

        # 8) Kick off AutoSell trailing monitor using the actual fill price
        try:
            from exec.auto_sell_logic import on_fill as autosell_on_fill
            anchor_price = price or (await get_token_price(token_address))
            if anchor_price:
                await autosell_on_fill(token_address, anchor_price)
        except Exception as e:
            logging.warning(f"[TradeExecutor] AutoSell on_fill failed: {e}")

    # ---------- pool/ATA watchers ----------

    async def _watch_pool_and_ata(self, token_mint: str, wallet_pubkey: str):
        """Resolve pool accounts for the mint (Raydium, then Orca fallback) and subscribe."""
        pool = None
        # Try Raydium
        try:
            from utils.raydium_sdk import get_pool_accounts_for_mint
            pool = await get_pool_accounts_for_mint(token_mint)
        except Exception:
            pool = None
        # Fallback: Orca
        if not isinstance(pool, dict):
            try:
                from utils.orca_sdk import get_pool_accounts_for_mint as orca_get
                pool = await orca_get(token_mint)
            except Exception:
                pool = None

        # Subscribe to pool accounts we care about
        try:
            if isinstance(pool, dict):
                for k in ("state", "vault_a", "vault_b", "lp_mint"):
                    acc = pool.get(k)
                    if isinstance(acc, str) and len(acc) > 20:
                        await request_account_watch(acc)
        except Exception as e:
            logging.warning(f"[TradeExecutor] pool account watch failed: {e}")

        # Subscribe to your ATA for this mint
        try:
            ata = derive_associated_token_account(wallet_pubkey, token_mint)
            if ata:
                await request_account_watch(ata)
        except Exception:
            pass

    async def _unwatch_pool_and_ata(self, token_mint: str, wallet_pubkey: str):
        """Optional cleanup if you want to stop streaming after exit."""
        try:
            from utils.raydium_sdk import get_pool_accounts_for_mint
            pool = await get_pool_accounts_for_mint(token_mint)
        except Exception:
            pool = None
        if not isinstance(pool, dict):
            try:
                from utils.orca_sdk import get_pool_accounts_for_mint as orca_get
                pool = await orca_get(token_mint)
            except Exception:
                pool = None

        try:
            if isinstance(pool, dict):
                for k in ("state", "vault_a", "vault_b", "lp_mint"):
                    acc = pool.get(k)
                    if isinstance(acc, str) and len(acc) > 20:
                        await stop_account_watch(acc)
        except Exception:
            pass

        try:
            ata = derive_associated_token_account(wallet_pubkey, token_mint)
            if ata:
                await stop_account_watch(ata)
        except Exception:
            pass

    # ---------- sell flow ----------

    async def sell_token(
        self,
        token_address: str,
        amount_tokens: Optional[float] = None,
        profit_percent: Optional[float] = None,
        wallet=None,
    ) -> Optional[str]:
        update_status("trade_executor")
        try:
            wallet_list = [wallet] if wallet else self.wallets
            for w in wallet_list:
                # Resolve amount if not provided
                amt = amount_tokens
                if amt is None:
                    try:
                        pos = open_position_tracker.get_position(w.address, token_address)
                        if pos:
                            amt = float(pos.get("amount", 0.0))
                        else:
                            amt = float((await w.get_token_balance(token_address)) or 0.0)
                    except Exception:
                        amt = 0.0
                if not amt or amt <= 0:
                    continue

                swap = await self._get_swap(w)
                tx_sig = await swap.sell_token(token_address, amt)
                if not tx_sig:
                    continue

                price = await get_token_price(token_address)
                self._record_trade("sell", token_address, amt, price, tx_sig, wallet=w.address)

                pnl_est = 0.0
                try:
                    pos = open_position_tracker.get_position(w.address, token_address)
                    if pos and price is not None:
                        pnl_est = (price - pos["price"]) * pos["amount"]

                    await multi_wallet.record_trade_pnl(
                        wallet=w,
                        pnl_sol=pnl_est,
                        was_profit=pnl_est > 0,
                        tx_sig=tx_sig,
                        token=token_address,
                    )
                    await tune_strategy({
                        "token_name": token_address,
                        "token_address": token_address,
                        "final_score": profit_percent or 0.0,
                        "outcome": "win" if pnl_est > 0 else "loss",
                        "pnl_pct": ((price - pos["price"]) / pos["price"]) * 100 if pos and pos["price"] else 0.0,
                    })
                    await handle_event({
                        "token": mint,
                        "action": "trade_close",
                        "meta": {"pnl": pnl, "roi": roi, "hold_time_s": hold_time},
                        "source": "trade_executor",
                    })
                    fs = get_feature_store_sync()
                    await fs.record_outcome(
                        token=mint,
                        side="sell" if closing else "buy",
                        pnl_pct=realized_pct,
                        slip_bps=slippage_bps,
                        hold_sec=hold_seconds,
                        arm=verdict.get("arm","balanced"),
                        profile=verdict.get("_profile",""),
                        score_at_entry=verdict.get("final_score", 0.0),
                        context={"intent_id": intent_id},
                    )

                    # optional clean-up
                    await self._unwatch_pool_and_ata(token_address, w.address)

                    decision_id = f"{token_address}-{int(datetime.utcnow().timestamp()*1000)}"
                    await event_bus().emit(TradeOutcomeEvent(
                        id=decision_id,
                        ts=now(),
                        token=token_address,
                        pnl=pnl_est,
                        holding_time_s=pos.get("holding_time", 0) if pos else 0,
                        strategy_type="sell",
                        meta={"wallet": w.address},
                    ))

                except Exception as e:
                    logging.warning(f"[TradeExecutor] PnL record/tuner feedback failed: {e}")

                return tx_sig

            log_event(f"❌ Sell failed for {token_address} — all wallets attempted.")
            return None

        except Exception as e:
            logging.exception(f"❌ Sell failed for {token_address}: {e}")
            return None

    # ---------- misc ----------

    def _record_trade(
        self,
        side: str,
        token_address: str,
        qty: float,
        price: Optional[float],
        tx_sig: Optional[str],
        source: str = "manual",
        category: Optional[str] = None,
        wallet: Optional[str] = None,
    ):
        try:
            payload = {
                "ts": datetime.utcnow().isoformat(),
                "type": "trade",
                "payload": {
                    "side": side,
                    "token": token_address,
                    "amount": qty,
                    "price": price,
                    "tx": tx_sig,
                    "source": source,
                    "category": category,
                    "wallet": wallet,
                },
            }
            log_event(json.dumps(payload))
        except Exception as e:
            logging.warning(f"[TradeExecutor] Trade record failed: {e}")

    async def try_auto_sell(self, token_address: str, reason: str = "auto_exit") -> bool:
        from exec.auto_sell_logic import execute_sell, should_sell_token
        from inputs.wallet.wallet_core import WalletManager
        from special.reverse_learning import record_exit_result

        try:
            wallet = WalletManager.get_default_wallet()
            token_info = await wallet.get_token_info(token_address)
            if not token_info or token_info.get("balance", 0) <= 0:
                log_event(f"[AutoSell] ⚠️ No balance for {token_address}")
                return False

            decision = await should_sell_token(token_address, token_info)
            if not decision.get("should_sell"):
                log_event(f"[AutoSell] ❌ Hold decision for {token_address}")
                return False

            sell_result = await execute_sell(wallet, token_address, token_info["balance"], reason=reason)
            pnl = sell_result.get("pnl", 0)

            record_exit_result(
                token_address, outcome="good_exit" if pnl > 0 else "bad_exit", pnl=pnl
            )
            log_event(f"[AutoSell] ✅ Sold {token_address} for {pnl:.4f} SOL (reason: {reason})")
            return True

        except Exception as e:
            log_event(f"[AutoSell] ❌ Failed for {token_address}: {e}")
            return False

    async def buy_nft(self, mint_address: str, reason="manual") -> Optional[str]:
        try:
            from exec.magic_eden_connector import buy_nft_me

            wallet = await wallet_auto_rotator.choose_wallet_for_strategy("nft", min_threshold=0.5)
            if not wallet:
                log_event("❌ No wallet available for NFT buy.")
                return None

            balance = await wallet.get_balance()
            if balance < 0.1:
                log_event(f"⚠️ Wallet {wallet.address} has insufficient balance for NFT buy.")
                return None

            tx_sig = await buy_nft_me(wallet, mint_address)
            if tx_sig:
                log_event(f"🎯 NFT Buy: {mint_address} | Wallet: {wallet.address}")
                await open_position_tracker.log_nft_buy(wallet.address, mint_address, tx_sig)
                await send_telegram_message(f"🖼️ Bought NFT: `{mint_address}` | Tx: {tx_sig}")
                return tx_sig

            log_event(f"❌ NFT Buy failed: {mint_address}")
            return None

        except Exception as e:
            logging.warning(f"[TradeExecutor] NFT Buy error: {e}")
            return None

    async def sell_nft(self, mint_address: str, wallet) -> Optional[str]:
        try:
            from exec.magic_eden_connector import list_nft_me

            # brain estimator (optional)
            price = None
            try:
                price = await self.brain.estimate_nft_value(mint_address)
            except Exception:
                pass

            if not price or price < 0.01:
                log_event(f"⚠️ NFT {mint_address} has no clear price. Skipping sell.")
                return None

            tx_sig = await list_nft_me(wallet, mint_address, price)
            if tx_sig:
                log_event(f"✅ NFT listed: {mint_address} at {price:.2f} SOL")
                await send_telegram_message(f"📤 Listed NFT `{mint_address}` for {price:.2f} SOL")
                return tx_sig
            else:
                log_event(f"❌ Failed to list NFT: {mint_address}")
                return None

        except Exception as e:
            logging.warning(f"[TradeExecutor] NFT Sell error: {e}")
            return None

    async def close(self):
        """Close the shared HTTP session."""
        try:
            if self.session and not self.session.closed:
                await self.session.close()
        except Exception:
            pass
