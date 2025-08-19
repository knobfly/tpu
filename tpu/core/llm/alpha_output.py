# modules/llm/alpha_output.py
# Turns model scores / decisions / outcomes into short human alpha calls & recaps.

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from core.llm.style_evolution import style_evolution

Decision = Literal["enter", "skip", "snipe", "watch", "trade", "ignore"]
Outcome = Literal["win", "loss", "pending"]

DEFAULT_THRESHOLDS = {
    "high": 0.80,   # fused_score/confidence >= high  => high conviction
    "mid":  0.60,   # >= mid => moderate conviction
}

# ------------ Public API ----------------------------------------------------

class AlphaOutput:
    def __init__(self, thresholds: Dict[str, float] = None):
        self.th = thresholds or DEFAULT_THRESHOLDS

    # For Trade/Snipe decision moment (called from evaluate_* or via EventBus TradeDecisionEvent)
    def generate_alpha_call(
        self,
        *,
        token: str,
        decision: Decision,
        fused_score: float,
        confidence: float,
        reason_tags: List[str] = None,
        insights: Dict[str, Any] = None,
        meta: Dict[str, Any] = None,
    ) -> str:
        reason_tags = reason_tags or []
        insights = insights or {}
        confidence_band = self._band(confidence)

        tone = style_evolution().current_tone()  # your style_evolution can expose this
        prefix = self._prefix(decision, confidence_band)
        core = self._compose_core(insights, reason_tags)
        suffix = self._suffix(decision, confidence_band)

        # Let style_evolution post-process phrasing
        msg = f"{prefix} {token}\n{core}\n{suffix}"
        return style_evolution().polish(msg, tone=tone)

    # For post-trade recap (called from TradeExecutor.finalize_trade or via TradeOutcomeEvent)
    def generate_trade_outcome(
        self,
        *,
        token: str,
        pnl: float,
        holding_time_s: float,
        strategy_type: str,
        decision_id: Optional[str] = None,
        meta: Dict[str, Any] = None,
    ) -> str:
        tone = style_evolution().current_tone()
        tag = "✅" if pnl > 0 else "❌"
        dur = self._fmt_duration(holding_time_s)
        extra = []
        if meta:
            if (mc := meta.get("model_confidence")) is not None:
                extra.append(f"conf={mc:.2f}")
            if (why := meta.get("keywords")):
                extra.append(f"tags: {', '.join(why[:5])}")
        extras = (" | " + " · ".join(extra)) if extra else ""

        msg = (
            f"{tag} {strategy_type.upper()} outcome on {token}\n"
            f"PnL: {pnl:.4f} SOL • Held: {dur}{extras}"
        )
        return style_evolution().polish(msg, tone=tone)

    # Optional: daily/periodic rollups
    def summarize_day(self, *, wins: int, losses: int, pnl_total: float) -> str:
        tone = style_evolution().current_tone()
        tag = "🔥" if pnl_total > 0 else "🩸"
        msg = f"{tag} Daily wrap: {wins}W / {losses}L | Net: {pnl_total:.2f} SOL"
        return style_evolution().polish(msg, tone=tone)

    # ------------ Internals -------------------------------------------------

    def _band(self, conf: float) -> Literal["high", "mid", "low"]:
        if conf >= self.th["high"]:
            return "high"
        if conf >= self.th["mid"]:
            return "mid"
        return "low"

    def _prefix(self, decision: Decision, band: str) -> str:
        if decision in ("snipe", "enter", "trade"):
            if band == "high":
                return "⚡ **High Conviction Entry**:"
            if band == "mid":
                return "🟡 **Moderate Entry**:"
            return "⚪ **Speculative Probe**:"
        if decision in ("watch", "skip", "ignore"):
            return "👀 **Watching**:" if decision == "watch" else "✋ **Skipping**:"
        return "🤖"

    def _compose_core(self, insights: Dict[str, Any], tags: List[str]) -> str:
        parts = []
        chart = insights.get("chart", {})
        wallet = insights.get("wallet", {})
        txn = insights.get("txn", {})

        if wallet.get("whales_present"):
            parts.append("whales spotted")
        if wallet.get("overlap_snipers", 0) > 0:
            parts.append(f"{wallet['overlap_snipers']} alpha snipers in")
        if chart.get("chart_score", 0) > 0:
            parts.append(f"chart+{chart['chart_score']}")
        if txn.get("sniper_pressure", 0) > 0:
            parts.append(f"sniper pressure {txn['sniper_pressure']}")

        tag_txt = f" | tags: {', '.join(tags[:5])}" if tags else ""
        return " • ".join(parts) + tag_txt if parts or tags else "no major signals"

    def _suffix(self, decision: Decision, band: str) -> str:
        if decision in ("snipe", "enter", "trade"):
            if band == "high":
                return "Risk/on. Executing."
            if band == "mid":
                return "Small size. Tight invalidation."
            return "Testing waters. Strict stop."
        if decision == "watch":
            return "Let it develop. Watching orderflow."
        return "Ignored."

    def _fmt_duration(self, secs: float) -> str:
        secs = int(secs or 0)
        if secs < 60:
            return f"{secs}s"
        mins = secs // 60
        if mins < 60:
            return f"{mins}m"
        hrs = mins // 60
        mins = mins % 60
        return f"{hrs}h {mins}m"


# Singleton
_ENGINE: Optional[AlphaOutput] = None

def alpha_output() -> AlphaOutput:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = AlphaOutput()
    return _ENGINE
