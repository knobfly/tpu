# utils/x_quota.py
import json
import math
import os
import time
from datetime import datetime, timezone

STATE_PATH = "runtime/memory/x_budget.json"

class XQuota:
    def __init__(self, monthly_quota: int = 15000, reserve_ratio: float = 0.10, rpm_soft_cap: int = 30):
        self.monthly_quota = monthly_quota
        self.reserve_ratio = reserve_ratio
        self.rpm_soft_cap = rpm_soft_cap
        self._state = {"month":"", "used":0, "minute":0, "minute_ts":0}
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        self._load()

    def _month_key(self):
        now = datetime.now(timezone.utc)
        return f"{now.year:04d}-{now.month:02d}"

    def _load(self):
        try:
            if os.path.exists(STATE_PATH):
                with open(STATE_PATH,"r") as f:
                    self._state = json.load(f)
        except: pass
        if self._state.get("month") != self._month_key():
            self._state = {"month": self._month_key(), "used":0, "minute":0, "minute_ts":0}
            self._save()

    def _save(self):
        try:
            with open(STATE_PATH,"w") as f:
                json.dump(self._state,f,indent=2)
        except: pass

    def remaining(self) -> int:
        self._load()
        return max(self.monthly_quota - int(self._state["used"]), 0)

    def allow(self, cost: int = 1) -> bool:
        """Refuse if monthly reserve would be broken or rpm soft cap exceeded."""
        self._load()
        rem = self.remaining()
        reserve_floor = math.ceil(self.monthly_quota * self.reserve_ratio)
        if rem - cost < reserve_floor:
            return False
        # rpm soft cap
        now = int(time.time())
        if now - int(self._state["minute_ts"]) >= 60:
            self._state["minute"] = 0
            self._state["minute_ts"] = now
        if self._state["minute"] + cost > self.rpm_soft_cap:
            return False
        return True

    def consume(self, cost: int = 1):
        self._load()
        self._state["used"] += cost
        now = int(time.time())
        if now - int(self._state["minute_ts"]) >= 60:
            self._state["minute"] = 0
            self._state["minute_ts"] = now
        self._state["minute"] += cost
        self._save()
