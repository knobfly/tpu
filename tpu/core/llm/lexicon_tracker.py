# modules/llm/lexicon_tracker.py
# Phase 5.1 — Lexicon Accumulator
# Logs new slang/memes/meta terms, tracks usage frequency + win/loss correlation,
# and can feed the personality/style systems.

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional

STATE_PATH = os.environ.get("NYX_LEXICON_STATE", "/home/ubuntu/nyx/runtime/nyx_data/lexicon_state.json")
LOCK = threading.RLock()

WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_\-]{2,32}")

# lightweight stoplist; extend from config if you want
STOP = {
    "the", "and", "for", "you", "with", "that", "this", "have", "has", "are",
    "was", "were", "but", "not", "from", "they", "them", "his", "her", "its",
    "your", "about", "into", "what", "when", "where", "why", "how", "who",
    "will", "would", "could", "should", "cant", "won't", "dont", "didnt",
    "https", "http"
}


@dataclass
class LexEntry:
    word: str
    first_seen: float
    last_seen: float
    sources: Dict[str, int] = field(default_factory=dict)      # source -> count
    uses: int = 0
    wins: int = 0
    losses: int = 0
    win_rate_impact: float = 0.0                               # smoothed lift vs. global baseline
    contexts: Dict[str, int] = field(default_factory=dict)     # optional tags
    meta: Dict[str, float] = field(default_factory=dict)       # free-form numeric metrics

    def record_source(self, src: str):
        self.sources[src] = self.sources.get(src, 0) + 1

    def record_context(self, ctx: Optional[str]):
        if not ctx:
            return
        self.contexts[ctx] = self.contexts.get(ctx, 0) + 1


class LexiconTracker:
    """
    - Extracts candidate words from raw text (Telegram, X feed, groups).
    - Tracks frequency and source.
    - Records win/loss associations to estimate win_rate_impact (Bayesian-smoothed).
    """

    def __init__(self, path: str = STATE_PATH, global_baseline: float = 0.52):
        self.path = path
        self.lex: Dict[str, LexEntry] = {}
        self.global_baseline = global_baseline  # your overall trade winrate
        self.alpha = 3.0                        # Bayesian prior strength
        self._load()

    # ---------------- public ----------------

    def add_from_text(
        self,
        text: str,
        source: str,
        context: Optional[str] = None,
        ts: Optional[float] = None,
        min_len: int = 3,
        max_len: int = 32,
    ) -> List[str]:
        """
        Parse text, record words, return list of words added/updated.
        """
        now = ts or time.time()
        words = self._extract_terms(text, min_len=min_len, max_len=max_len)
        updated: List[str] = []
        with LOCK:
            for w in words:
                if w in STOP:
                    continue
                e = self.lex.get(w)
                if not e:
                    e = LexEntry(word=w, first_seen=now, last_seen=now)
                    self.lex[w] = e
                else:
                    e.last_seen = now
                e.uses += 1
                e.record_source(source)
                e.record_context(context)
                updated.append(w)
        if updated:
            self._save()
        return updated

    def record_outcome(self, words: Iterable[str], win: bool) -> None:
        """
        After a trade closes (or signal validated), associate outcome with the words that were part of the decision context.
        """
        with LOCK:
            for w in words:
                e = self.lex.get(w)
                if not e:
                    continue
                if win:
                    e.wins += 1
                else:
                    e.losses += 1
                e.win_rate_impact = self._bayesian_lift(e.wins, e.losses)
        self._save()

    def top_terms(self, n: int = 25, min_uses: int = 3, sort_by: str = "win_rate_impact") -> List[LexEntry]:
        """
        Return best terms by impact or frequency.
        """
        with LOCK:
            items = [e for e in self.lex.values() if e.uses >= min_uses]
            if sort_by == "frequency":
                items.sort(key=lambda x: x.uses, reverse=True)
            else:
                items.sort(key=lambda x: x.win_rate_impact, reverse=True)
            return items[:n]

    def export_for_personality(self, min_impact: float = 0.02, min_uses: int = 3, top_k: int = 50) -> List[str]:
        """
        Words worth biasing into `personality_core` vocabulary.
        """
        return [e.word for e in self.top_terms(n=top_k) if e.win_rate_impact >= min_impact and e.uses >= min_uses]

    def vocab_snapshot(self) -> Dict[str, dict]:
        with LOCK:
            return {w: asdict(e) for w, e in self.lex.items()}

    # ---------------- internals ----------------

    def _bayesian_lift(self, wins: int, losses: int) -> float:
        """
        Posterior mean - baseline (so positive = helpful).
        Uses a Beta prior centered at the global baseline win-rate.
        """
        a0 = self.alpha * self.global_baseline
        b0 = self.alpha * (1.0 - self.global_baseline)
        post = (wins + a0) / (wins + losses + a0 + b0)
        return post - self.global_baseline

    def _extract_terms(self, text: str, min_len: int, max_len: int) -> List[str]:
        text = text.lower()
        terms = []
        for m in WORD_RE.finditer(text):
            w = m.group(0)
            if min_len <= len(w) <= max_len:
                terms.append(w)
        return terms

    def _save(self) -> None:
        with LOCK:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({w: asdict(e) for w, e in self.lex.items()}, f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
            for w, d in data.items():
                self.lex[w] = LexEntry(**d)
        except Exception:
            # start clean if corrupted
            self.lex = {}


# ---------------- singleton helpers ----------------

_TRACKER: Optional[LexiconTracker] = None


def init_lexicon_tracker(path: str = STATE_PATH, global_baseline: float = 0.52) -> LexiconTracker:
    global _TRACKER
    if _TRACKER is None:
        _TRACKER = LexiconTracker(path=path, global_baseline=global_baseline)
    return _TRACKER


def lexicon_tracker() -> LexiconTracker:
    if _TRACKER is None:
        raise RuntimeError("LexiconTracker not initialized. Call init_lexicon_tracker() first.")
    return _TRACKER
