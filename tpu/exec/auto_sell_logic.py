import asyncio
import inspect
import logging
import time
from datetime import datetime, timedelta
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional

from core.live_config import config as live_config
from exec.trade_executor import TradeExecutor
from inputs.wallet.multi_wallet_manager import multi_wallet
from inputs.wallet.wallet_core import WalletManager
from special.ai_self_tuner import learn_from_result as record_ai_result
from special.insight_logger import log_sell_insight
from strategy.stop_snipe_defender import register_rug_event
from strategy.strategy_memory import record_result
from utils.logger import log_event
from utils.price_fetcher import get_token_price
from utils.service_status import update_status
from utils.token_utils import (
    add_to_blacklist,
    detect_honeypot,
    detect_rug_behavior,
    get_wallet_tokens,
)


class AutoSellLogic:
    """
    Auto-sell supervisor:
      - Hard rules (profit target, max loss, score decay, post-moon dump)
      - Continuous trailing monitor (dynamic)
      - Rug/honeypot defenses
    Integration:
      - Call `on_fill(token_address, buy_price)` after a successful BUY to start monitoring that token.
      - Run `run()` as a long-lived task for periodic rule checks.
    """
    def __init__(self, wallet: WalletManager):
        self.wallet = wallet
        # token_address -> trailing state
        self.trailing_data: Dict[str, Dict[str, Any]] = {}
        self.interval = 60
        # simple per-token cooldown for evaluate_tokens (not the trailing loop)
        self.sell_cache: Dict[str, datetime] = {}
        self._executor = None
        self.cooldowns = {}
        self._sem = asyncio.Semaphore(10)

    # ---------- External hooks ----------
    async def on_fill(self, token_address: str, buy_price: float) -> None:
        """Hook this after each successful buy. Starts the trailing monitor for that token."""
        if not token_address or buy_price is None:
            return
        if token_address in self.trailing_data:
            # already monitoring; update anchor if needed
            td = self.trailing_data[token_address]
            td.setdefault("bought_at", buy_price)
            return
        asyncio.create_task(self.monitor(token_address, buy_price), name=f"autosell:monitor:{token_address}")

    def _cooldown(self, addr: str, seconds: int = 300) -> bool:
        now = datetime.utcnow().timestamp()
        last = self._cooldowns.get(addr)
        if last and (now - last) < seconds:
            return True
        self._cooldowns[addr] = now
        return False

    # ---------- Supervisor loop ----------
    async def run(self):
        log_event("💸 Auto-Sell Logic active.")
        while True:
            try:
                await self.evaluate_tokens()
            except Exception as e:
                logging.warning(f"[AutoSell] Error: {e}")
            await asyncio.sleep(self.interval)

    async def _get_executor(self):
        if self._executor is None:
            from exec.trade_executor import TradeExecutor
            self._executor = TradeExecutor(self.wallet)
        return self._executor

    async def evaluate_tokens(self):
        update_status("auto_sell_logic")

        try:
            if inspect.iscoroutinefunction(get_wallet_tokens):
                tokens = await get_wallet_tokens(self.wallet)
            else:
                tokens = get_wallet_tokens(self.wallet)

            for tk in tokens or []:
                addr = tk.get("address") or tk.get("mint")
                if not addr:
                    continue
                if self._cooldown(addr, seconds=300):
                    continue
                async with self._sem:
                    await self._evaluate_token_rules(tk)

                await asyncio.sleep(0.03)
        except Exception as e:
            logging.warning(f"[AutoSell] evaluate_tokens failed: {e}")

    async def _evaluate_token_rules(self, tk: dict):
        """
        Maps token dicts from utils.token_utils.get_wallet_tokens() into the
        fields your existing evaluate_token() expects, then calls it.
        """
        addr = tk.get("address") or tk.get("mint")
        if not addr:
            return

        token_stub = {
            "address": addr,
            "mint": addr,
            "profit_percent": float(tk.get("profit_percent") or tk.get("pnl_pct") or 0.0),
            "score": float(tk.get("score") or 50.0),
            "age_minutes": float(tk.get("age_minutes") or 0.0),
            "bought_at": tk.get("bought_at"),
            "price": tk.get("last_price") or tk.get("price"),
            "buy_amount_sol": float(tk.get("buy_amount_sol") or 0.1),
            "last_tx": tk.get("last_tx"),
        }

        try:
            await self.evaluate_token(token_stub)
        except Exception as e:
            logging.warning(f"[AutoSell] rule eval failed for {addr}: {e}")

        # === AI Safety Override ===
        if score < 30 and age > 5:
            await self._force_exit_dict(token, "low_score_exit")
            return

        # === Hard Rules ===
        if profit >= float(live_config.get("sell_profit_percent", 30)):
            await self._force_exit_dict(token, "target_profit_hit")
            return

        if profit <= float(live_config.get("max_loss_percent", -40)):
            await self._force_exit_dict(token, "max_loss_limit")
            return

        if score < 35 and age > 15:
            await self._force_exit_dict(token, "score_decay_exit")
            return

        if float(token.get("peak_profit", 0) or 0) >= 70 and profit < 10:
            await self._force_exit_dict(token, "post_moon_dump")
            return

    # ---------- Trailing monitor (runs per token) ----------
    async def monitor(self, token_address: str, buy_price: float) -> None:
        """Continuous trailing stop + defenses for a single token."""
        update_status("auto_sell_logic")
        token_acct = await self.wallet.get_token_account(token_address)

        if not buy_price:
            current = await get_token_price(token_address)
            if not current:
                logging.warning(f"[AutoSell] Cannot monitor {token_address} — price not found.")
                return
            buy_price = current

        td = self.trailing_data[token_address] = {
            "peak": float(buy_price),
            "bought_at": float(buy_price),
            "timestamps": [time.time()],
            "prices": [float(buy_price)],
            "started_at": time.time(),
            "breakeven_lift_done": False,   # once profit > X%, ratchet stop to BE
        }

        cfg = live_config.get("trailing_stop", {}) or {}
        base_drop_pct = float(cfg.get("drop_pct", 10)) / 100.0
        trigger_pct = float(cfg.get("trigger_pct", 20)) / 100.0
        tw_exit_s = int(live_config.get("time_weighted_exit_seconds", 180))
        min_profit = float(live_config.get("sell_profit_percent", 30)) / 100.0
        deadline = td["started_at"] + tw_exit_s

        # dynamic adjustments
        dyn_window = int(cfg.get("dynamic_window", 30))
        dyn_sensitivity = float(cfg.get("dynamic_sensitivity", 0.75))  # multiplies drop based on recent vol

        try:
            while True:
                price = await get_token_price(token_address)
                if not price:
                    await asyncio.sleep(2)
                    continue

                now = time.time()
                td["timestamps"].append(now)
                td["prices"].append(float(price))
                if price > td["peak"]:
                    td["peak"] = float(price)

                change = (price - td["bought_at"]) / td["bought_at"]
                drop_from_peak = (td["peak"] - price) / max(td["peak"], 1e-9)

                # ---- Dynamic drop based on recent volatility ----
                recent = td["prices"][-dyn_window:] if len(td["prices"]) >= dyn_window else td["prices"]
                dyn_drop = base_drop_pct
                if len(recent) >= 5:
                    vol = pstdev(recent) / max(min(recent), 1e-9)
                    # keep it modest; rising vol => allow a bit more give
                    dyn_drop = min(0.25, max(base_drop_pct * (1.0 + dyn_sensitivity * vol), base_drop_pct * 0.6))

                rsi = self.calculate_rsi(td["prices"][-30:])
                ema_seq = self.calculate_ema(td["prices"][-20:])

                insight = {
                    "token": token_address,
                    "price": float(price),
                    "bought_at": float(td["bought_at"]),
                    "peak": float(td["peak"]),
                    "rsi": rsi,
                    "ema_tail": ema_seq[-3:] if ema_seq else [],
                    "drop_from_peak_pct": round(drop_from_peak * 100, 2),
                    "dynamic_drop_pct": round(dyn_drop * 100, 2),
                    "profit_percent": round(change * 100, 2),
                    "elapsed_s": int(now - td["started_at"]),
                }

                # ---- Rug / Honeypot defenses ----
                if detect_rug_behavior(token_address, price, td["prices"]):
                    await self._force_exit_str(token_address, token_acct, "rug_detected", insight)
                    register_rug_event(token_address)
                    break

                if detect_honeypot(token_address, getattr(self.wallet, "keypair", None), live_config):
                    add_to_blacklist(token_address)
                    await self._force_exit_str(token_address, token_acct, "honeypot", insight)
                    register_rug_event(token_address)
                    break

                # ---- Triggered trailing: first hit trigger_pct profit, then give dyn_drop room ----
                if change >= trigger_pct and drop_from_peak >= dyn_drop:
                    await self._force_exit_str(token_address, token_acct, "trailing_stop_triggered", insight)
                    break

                # ---- Breakeven lift: once we’re up 10% (configurable), raise stop to >= 0% ----
                be_lift_at = float(cfg.get("breakeven_lift_at_pct", 10)) / 100.0
                if not td["breakeven_lift_done"] and change >= be_lift_at:
                    # lift "bought_at" reference slightly to reduce chance of round-trip
                    td["bought_at"] = min(td["bought_at"], price * 0.995)
                    td["breakeven_lift_done"] = True

                # ---- RSI momentum fade exit ----
                if rsi is not None:
                    rsi_prev = self.calculate_rsi(td["prices"][-32:-2])
                    if rsi_prev and rsi > 70 and rsi < rsi_prev:
                        await self._force_exit_str(token_address, token_acct, "rsi_drop_detected", insight)
                        break

                # ---- EMA short-term rollover ----
                if ema_seq and len(ema_seq) >= 3 and (ema_seq[-1] < ema_seq[-2] < ema_seq[-3]):
                    await self._force_exit_str(token_address, token_acct, "ema_reversal", insight)
                    break

                # ---- Time-weighted exit if already profitable ----
                if now > deadline and change >= min_profit:
                    await self._force_exit_str(token_address, token_acct, "time_weighted_exit", insight)
                    break

                await asyncio.sleep(2)
        finally:
            # cleanup on any exit or exception
            self.trailing_data.pop(token_address, None)

    # ---------- Exit helpers ----------
    async def _force_exit_dict(self, token: Dict[str, Any], reason: str):
        addr = token.get("address") or token.get("mint")
        if not addr:
            return
        token_acct = await self.wallet.get_token_account(addr)
        pct = float(token.get("profit_percent", 0) or 0.0)
        await self._sell_and_record(addr, token_acct, pct, reason, extra=token)

    async def _force_exit_str(self, token_address: str, token_acct: Any, reason: str, insight: Dict[str, Any]):
        pct = float(insight.get("profit_percent", 0) or 0.0)
        await self._sell_and_record(token_address, token_acct, pct, reason, extra=insight)

    async def _sell_and_record(
        self,
        token_address: str,
        token_acct: Any,           # kept for API compatibility; not required in this path
        profit_pct: float,
        reason: str,
        extra: Optional[Dict[str, Any]] = None,
    ):
        logging.info(f"💡 Auto-sell triggered for {token_address} due to {reason}")
        try:
            executor = await self._get_executor()
            # fetch live balance to know how much to sell
            info = await self.wallet.get_token_info(token_address)
            amount_tokens = (info or {}).get("balance") or (info or {}).get("amount") or 0.0
            if amount_tokens and amount_tokens > 0:
                await executor.sell_token(
                    token_address,
                    amount_tokens,
                    profit_percent=profit_pct,   # <-- use the arg you computed
                    wallet=self.wallet,
                )
        except Exception as e:
            logging.warning(f"[AutoSell] sell_token failed: {e}")

        await self._record_trade_feedback(token_address, profit_pct, reason, extra)

     # Make _record_trade_feedback accept address + optional extra dict (or old dict)
    async def _record_trade_feedback(
        self,
        token_or_addr: Any,
        profit_percent: float,
        reason: str,
        extra: Optional[Dict[str, Any]] = None,
    ):
        """
        token_or_addr: either a token dict (legacy) or a token address (str)
        extra: optional dict carrying fields like bought_at, price, buy_amount_sol, etc.
        """
        try:
            # normalize inputs
            if isinstance(token_or_addr, dict):
                token_addr = token_or_addr.get("address") or token_or_addr.get("mint") or ""
                meta = token_or_addr
            else:
                token_addr = str(token_or_addr)
                meta = extra or {}

            # prices & amounts (best-effort)
            buy_price = meta.get("bought_at")
            if buy_price is None:
                # fetch from wallet position if possible
                try:
                    pos = await self.wallet.get_token_info(token_addr)
                    buy_price = (pos or {}).get("avg_entry_price") or (pos or {}).get("bought_at")
                except Exception:
                    buy_price = None

            current_price = meta.get("price")
            if current_price is None:
                try:
                    from utils.price_fetcher import get_token_price
                    current_price = await get_token_price(token_addr)
                except Exception:
                    current_price = None

            buy_amount_sol = meta.get("buy_amount_sol")
            if buy_amount_sol is None:
                # fall back to position sizing if available
                try:
                    pos = await self.wallet.get_token_info(token_addr)
                    buy_amount_sol = (pos or {}).get("buy_amount_sol") or 0.0
                except Exception:
                    buy_amount_sol = 0.0

            pnl_sol = 0.0
            if buy_price and current_price and buy_amount_sol:
                pnl_sol = ((current_price - buy_price) / buy_price) * float(buy_amount_sol)

            # multi-wallet PnL + AI feedback + logs
            try:
                from inputs.wallet.multi_wallet_manager import multi_wallet
                await multi_wallet.record_trade_pnl(
                    wallet=self.wallet,
                    pnl_sol=pnl_sol,
                    was_profit=pnl_sol > 0,
                    tx_sig=meta.get("last_tx", "unknown"),
                    token=token_addr,
                )
            except Exception as e:
                logging.warning(f"[AutoSell] PnL record error: {e}")

            try:
                from strategy.strategy_memory import record_result
                record_result(token_addr, reason)
            except Exception:
                pass

            try:
                from special.insight_logger import log_sell_insight
                log_sell_insight(token_addr, reason, {"pnl_sol": pnl_sol, **meta})
            except Exception:
                pass

            try:
                from special.ai_self_tuner import learn_from_result as record_ai_result
                record_ai_result(token_addr, profit_percent)
            except Exception:
                pass

        except Exception as e:
            logging.warning(f"[AutoSell] Feedback record error: {e}")

    # ---------- Indicators ----------
    def calculate_rsi(self, prices: List[float], period: int = 14) -> Optional[float]:
        if not prices or len(prices) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(prices)):
            diff = prices[i] - prices[i - 1]
            if diff > 0:
                gains.append(diff)
            elif diff < 0:
                losses.append(-diff)
        avg_gain = mean(gains[-period:]) if gains else 0.0
        avg_loss = mean(losses[-period:]) if losses else 0.0
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def calculate_ema(self, prices: List[float], period: int = 10) -> List[float]:
        if not prices or len(prices) < period:
            return []
        ema = [prices[0]]
        k = 2.0 / (period + 1.0)
        for p in prices[1:]:
            ema.append((p * k) + (ema[-1] * (1.0 - k)))
        return ema


# ---------- Lightweight helper for other modules ----------
async def evaluate_early_exit(token_address: str, wallet: WalletManager, cfg) -> bool:
    """
    Quick one-off decision. Returns True if an early exit should be taken.
    Looks for drop-from-peak using any active trailing cache if present.
    """
    try:
        price = await get_token_price(token_address)
        if not price:
            return False

        drop_threshold = float(cfg.get("early_exit_drop_pct", 15)) / 100.0
        profit_target = float(cfg.get("early_exit_profit_pct", 30)) / 100.0

        # If caller is using a shared AutoSellLogic instance, they should pass its trailing_data in cfg.
        trailing: Dict[str, Any] = (cfg.get("trailing_data") or {})
        td = trailing.get(token_address)

        peak = float(td.get("peak")) if td else price
        bought = float(td.get("bought_at")) if td else price
        drop_from_peak = (peak - price) / max(peak, 1e-9)
        change = (price - bought) / max(bought, 1e-9)

        if drop_from_peak >= drop_threshold:
            logging.info(f"[AutoSellLogic] Early exit {token_address}: drop_from_peak={drop_from_peak*100:.2f}%")
            return True
        if change >= profit_target:
            logging.info(f"[AutoSellLogic] Early exit {token_address}: profit_target hit ({change*100:.2f}%)")
            return True
        return False
    except Exception as e:
        logging.warning(f"[AutoSellLogic] evaluate_early_exit error: {e}")
        return False


# --- module-level singleton + tiny facade ---

autosell_logic: AutoSellLogic | None = None

async def start_autosell(wallet):
    """Create and start the shared AutoSellLogic loop once."""
    global autosell_logic
    if autosell_logic is None:
        autosell_logic = AutoSellLogic(wallet)
        # background loop
        asyncio.create_task(autosell_logic.run())
    return autosell_logic

async def on_fill(token_address: str, anchor_price: float):
    """Record a fresh fill into the trailing monitor (if running)."""
    if autosell_logic:
        # fast path: wire into the existing monitor state
        autosell_logic.trailing_data[token_address] = {
            "peak": anchor_price,
            "bought_at": anchor_price,
            "timestamps": [time.time()],
            "prices": [anchor_price],
        }
