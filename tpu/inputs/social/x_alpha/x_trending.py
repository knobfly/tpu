# /x_trending.py
from __future__ import annotations
import json, math, os, time
from typing import Dict, Iterable, List, Optional, Tuple

STATE_PATH = "runtime/library/x_trending.json"
MAX_RULE_LEN = 512

def _now() -> float: return time.time()

class TrendingTerms:
    """
    Tracks keyword productivity over time and returns the best subset for X rules.
    Score favors terms that yield usable data, with decay so stale terms drop off.
    """
    def __init__(self, path: str = STATE_PATH, half_life_sec: int = 6*3600, floor_hits: float = 3.0):
        self.path = path
        self.half_life = float(half_life_sec)      # exponential decay half-life
        self.floor = float(floor_hits)             # small prior to stabilize early ratios
        self.terms: Dict[str, Dict[str, float]] = {}
        self._load()

    # ---------- persistence ----------
    def _load(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self.terms = json.load(f) or {}
        except Exception:
            self.terms = {}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.terms, f, indent=2)
        except Exception:
            pass

    # ---------- updates ----------
    def register_observation(self, term: str, usable: bool, ts: Optional[float] = None):
        """
        Called per tweet processed that matched `term`. `usable=True` if your pipeline
        considered it watch/alert/quote-worthy (whatever your criteria).
        """
        ts = ts or _now()
        t = term.strip()
        if not t: return

        rec = self.terms.get(t) or {"hits": 0.0, "usable": 0.0, "last": ts}
        # decay old mass before adding new evidence
        rec = self._decay_record(rec, ts)
        rec["hits"] += 1.0
        if usable:
            rec["usable"] += 1.0
        rec["last"] = ts
        self.terms[t] = rec

    def _decay_record(self, rec: Dict[str, float], ts: float) -> Dict[str, float]:
        dt = max(0.0, ts - float(rec.get("last", ts)))
        if dt <= 0: return rec
        # exponential decay to half in half_life
        decay = 0.5 ** (dt / self.half_life) if self.half_life > 0 else 1.0
        rec["hits"]   = rec.get("hits",   0.0) * decay
        rec["usable"] = rec.get("usable", 0.0) * decay
        return rec

    # ---------- ranking ----------
    def score(self, term: str, ts: Optional[float] = None) -> float:
        ts = ts or _now()
        rec = self.terms.get(term)
        if not rec: return 0.0
        rec = self._decay_record(rec, ts)
        hits   = rec["hits"]
        usable = rec["usable"]
        # Bayesian-ish: (usable + prior) / (hits + prior*2), scaled by evidence
        quality = (usable + self.floor) / (hits + 2.0*self.floor)
        # evidence factor: more total observations => more weight
        evidence = 1.0 - math.exp(-hits / 10.0)
        return float(quality * (0.6 + 0.4 * evidence))  # 0..1

    def top_terms(self, limit: int, whitelist: Optional[Iterable[str]] = None) -> List[str]:
        ts = _now()

        # Normalize whitelist and pre-seed missing terms so we never KeyError later.
        wl: set[str] = set()
        if whitelist:
            for w in whitelist:
                if not w:
                    continue
                t = str(w).strip()
                if not t:
                    continue
                wl.add(t)
                if t not in self.terms:
                    self.terms[t] = {"hits": 0.0, "usable": 0.0, "last": ts}

        # Rank all known terms (including any just seeded from whitelist)
        candidates = list(self.terms.keys())
        scored = [(t, self.score(t, ts)) for t in candidates]
        scored.sort(key=lambda kv: kv[1], reverse=True)
        top = [t for t, _ in scored[:max(0, limit)]]

        # Build keep-set: top terms + anything with recent evidence + whitelist
        keep = set(top) | wl
        for k, rec in self.terms.items():
            try:
                if ts - float(rec.get("last", ts)) < self.half_life:
                    keep.add(k)
            except Exception:
                # if malformed, keep it for one more round rather than drop silently
                keep.add(k)

        # Safe prune: only keep keys that currently exist, defaulting if somehow missing
        new_terms: Dict[str, Dict[str, float]] = {}
        for k in keep:
            if k in self.terms:
                new_terms[k] = self.terms[k]
            else:
                new_terms[k] = {"hits": 0.0, "usable": 0.0, "last": ts}
        self.terms = new_terms

        self._save()
        return top

    # ---------- rule builder ----------
    def build_rules(self, terms: List[str], filters: str = "-is:retweet -is:reply lang:en",
                    max_len: int = MAX_RULE_LEN) -> List[str]:
        rules: List[str] = []
        group: List[str] = []

        def rule_len(parts: List[str]) -> int:
            if not parts: return 0
            body = " OR ".join(parts)
            return 1 + len(body) + 2 + len(filters)  # "(" + body + ") " + filters

        for t in terms:
            t = t.strip()
            if not t: continue
            # start or extend
            if not group:
                if rule_len([t]) <= max_len:
                    group = [t]; continue
                # degenerate single long token (rare): push raw
                rules.append(f"{t} {filters}"[:max_len]); group = []; continue
            if rule_len(group + [t]) <= max_len:
                group.append(t)
            else:
                rules.append(f"({' OR '.join(group)}) {filters}")
                group = [t] if rule_len([t]) <= max_len else []
                if not group:
                    rules.append(f"{t} {filters}"[:max_len])

        if group:
            rules.append(f"({' OR '.join(group)}) {filters}")
        return rules
