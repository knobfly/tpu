# librarian_index.py
# Event handling and indexing logic split from data_librarian.py

from librarian.librarian_models import TokenRecord, WalletRecord
from datetime import datetime
import time
import logging
from typing import Optional

class LibrarianIndex:
    def __init__(self, tokens, wallets, events_by_type, counters):
        self._tokens = tokens
        self._wallets = wallets
        self._events_by_type = events_by_type
        self._counters = counters

    def handle_scoring_event(self, payload: dict, ev: dict, token: Optional[str]):
        if not token:
            return
        rec = self._tokens.setdefault(token, TokenRecord(token=token))
        score_obj = {
            "ts": ev["ts"],
            "final_score": payload.get("final_score") or payload.get("score") or 0.0,
            "engine": payload.get("_scoring_engine"),
            "raw": payload,
        }
        rec.scores.append(score_obj)
        rec.last_ts = ev["ts"]
        src = payload.get("source") or ev.get("_src")
        if src:
            rec.scanners.add(src)

    def handle_trade_event(self, payload: dict, ev: dict, token: Optional[str], wallet: Optional[str]):
        if token:
            rec = self._tokens.setdefault(token, TokenRecord(token=token))
            rec.tags.add("traded")
            rec.last_ts = ev["ts"]
        if wallet:
            wrec = self._wallets.setdefault(wallet, WalletRecord(wallet=wallet))
            wrec.events.append(ev)
            wrec.last_ts = ev["ts"]

    def handle_wallet_event(self, payload: dict, ev: dict, token: Optional[str], wallet: Optional[str]):
        if wallet:
            wrec = self._wallets.setdefault(wallet, WalletRecord(wallet=wallet))
            wrec.events.append(ev)
            wrec.last_ts = ev["ts"]
        if token:
            trec = self._tokens.setdefault(token, TokenRecord(token=token))
            trec.last_ts = ev["ts"]
            src = payload.get("source") or ev.get("_src")
            if src:
                trec.scanners.add(src)

    def handle_chart_event(self, payload: dict, ev: dict, token: Optional[str]):
        if not token:
            return
        rec = self._tokens.setdefault(token, TokenRecord(token=token))
        rec.chart = {
            "pattern": payload.get("pattern"),
            "confidence": payload.get("confidence"),
            "trend": payload.get("trend"),
            "timing": payload.get("timing"),
            "recent_price": payload.get("recent_price"),
            "volume": payload.get("volume"),
            "last_ts": ev["ts"],
        }
        rec.last_ts = ev["ts"]

    def index_token_event(self, token: str, ev: dict):
        rec = self._tokens.setdefault(token, TokenRecord(token=token))
        rec.events.append(ev)
        rec.last_ts = max(rec.last_ts, ev["ts"])
        tag = ev["payload"].get("tag") or ev["payload"].get("result")
        if tag:
            rec.tags.add(str(tag))
        src = ev["payload"].get("source") or ev.get("_src")
        if src:
            rec.scanners.add(src)
        meta = ev["payload"].get("metadata") or ev["payload"].get("meta")
        if isinstance(meta, dict) and meta:
            rec.meta.update(meta)

    def index_wallet_event(self, wallet: str, ev: dict):
        rec = self._wallets.setdefault(wallet, WalletRecord(wallet=wallet))
        rec.events.append(ev)
        rec.last_ts = max(rec.last_ts, ev["ts"])
        tag = ev["payload"].get("tag")
        if tag:
            rec.tags.add(str(tag))
        cluster_id = ev["payload"].get("cluster_id")
        if cluster_id:
            rec.clusters.add(str(cluster_id))
        meta = ev["payload"].get("metadata") or ev["payload"].get("meta")
        if isinstance(meta, dict) and meta:
            rec.meta.update(meta)
