import logging
from datetime import datetime, timedelta


class HeliusBudgetGuard:
    def __init__(self, max_units_per_month: int = 100_000_000):
        self.max_units = max_units_per_month
        self.used_units = 0
        self.reset_time = datetime.utcnow().replace(day=1) + timedelta(days=32)
        self.reset_time = self.reset_time.replace(day=1)
        self.usage_log = []

    def _reset_if_needed(self):
        if datetime.utcnow() >= self.reset_time:
            self.used_units = 0
            self.usage_log.clear()
            self.reset_time = datetime.utcnow().replace(day=1) + timedelta(days=32)
            self.reset_time = self.reset_time.replace(day=1)

    def allow_usage(self, expected_cost: int = 0, minimum_confidence: float = 0.0) -> bool:
        self._reset_if_needed()

        projected = self.used_units + expected_cost
        usage_pct = projected / self.max_units

        # 🟢 Allow low confidence calls if usage is under 70%
        if usage_pct < 0.7:
            return True

        # 🟡 After 70%, require stronger confidence
        if usage_pct < 0.9:
            return minimum_confidence >= 0.5

        # 🔴 After 90%, only allow high importance
        return minimum_confidence >= 0.9

    def record_usage(self, units: int):
        self._reset_if_needed()
        self.used_units += units
        self.usage_log.append((datetime.utcnow(), units))

    def get_usage_summary(self):
        self._reset_if_needed()
        return {
            "used": self.used_units,
            "limit": self.max_units,
            "remaining": self.max_units - self.used_units,
            "percent": round((self.used_units / self.max_units) * 100, 2)
        }
