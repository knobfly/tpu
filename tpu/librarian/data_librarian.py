#/librarian/data_librarian.py
# -----------------------------------------------------------------------------
# The Librarian: central, opinionated data organizer for Nyx.
#
# - Continuously ingests from JSONL log directories (incremental tail with offsets)
# - Optionally mirrors all live events from signal_bus (attach_bus)
# - Normalizes & indexes data by token, wallet, signal type
# - Exposes fast query APIs for Cortexes
# -----------------------------------------------------------------------------
from __future__ import annotations

import asyncio
import gzip
import io
import json
import logging
import glob
import os
import re
import shutil
import time
import contextlib

from librarian.librarian_stream import LibrarianStream
from librarian.librarian_token import LibrarianToken
from librarian.librarian_wallet import LibrarianWallet
from librarian.librarian_nlp import LibrarianNLP
from librarian.librarian_telegram import LibrarianTelegram
from librarian.librarian_config import RUNTIME_ROOT, LOGS_ROOT, LIBRARY_ROOT, GENRES, JSONL_SOURCES, DISK_SCAN_INTERVAL_SEC, STATUS_HEARTBEAT_SECONDS, MAX_EVENTS_PER_TYPE, MAX_TOKEN_EVENTS, MAX_WALLET_EVENTS
from librarian.librarian_utils import find_token, find_wallet, safe_read_json_dict
from librarian.librarian_models import TokenRecord, WalletRecord
from librarian.librarian_learning import LibrarianLearning
from librarian.librarian_chat import LibrarianChat
from librarian.librarian_maintenance import LibrarianMaintenance
from librarian.librarian_utils import find_token, find_wallet, safe_read_json_dict

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Any, Deque, Dict, List, Optional, Set, Tuple
from glob import glob as _glob

import psutil
from core.live_config import config
from librarian.rules import telegram_auto_track
from utils.logger import log_event
from utils.service_status import update_status



class DataLibrarian:
    def catalog_influencer(self, influencer: Dict[str, Any]):
        """
        Catalog a Telegram influencer/admin profile for analytics and scoring.
        """
        if not hasattr(self, 'influencer_store'):
            self.influencer_store = {}
        user = influencer.get('user')
        if user:
            self.influencer_store[user] = influencer
            log_event(f"[Librarian] Cataloged influencer: {user}")

    def blacklist_source(self, source: Dict[str, Any]):
        """
        Blacklist a Telegram user/group for scam/rug detection.
        """
        if not hasattr(self, 'blacklist_store'):
            self.blacklist_store = {}
        user = source.get('user')
        group = source.get('group')
        key = f"{group}:{user}"
        self.blacklist_store[key] = source
        log_event(f"[Librarian] Blacklisted source: {key}")
    def ingest_telegram_message(self, msg: Dict[str, Any]):
        """
        Ingest a structured Telegram message and update all relevant profiles.
        msg: {
            'group': str,
            'user': str,
            'text': str,
            'keywords': List[str],
            'sentiment': float,
            'wallets': List[str],
            'tokens': List[str],
            'timestamp': str
        }
        """
        group = msg.get('group')
        user = msg.get('user')
        text = msg.get('text')
        keywords = msg.get('keywords', [])
        sentiment = msg.get('sentiment')
        wallets = msg.get('wallets', [])
        tokens = msg.get('tokens', [])
        timestamp = msg.get('timestamp')

        # User profile/activity
        if user:
            profile = {
                'last_message': text,
                'last_group': group,
                'last_keywords': keywords,
                'last_sentiment': sentiment,
                'last_wallets': wallets,
                'last_tokens': tokens,
                'last_timestamp': timestamp
            }
            self.ingest_telegram_user(user, profile)
            self.update_telegram_activity(user, {
                'group': group,
                'text': text,
                'keywords': keywords,
                'sentiment': sentiment,
                'wallets': wallets,
                'tokens': tokens,
                'timestamp': timestamp
            })

        # Token profile/mentions
        for token in tokens:
            self.token.ingest_token_profile({
                'contract': token,
                'source': 'telegram',
                'last_mentioned_by': user,
                'last_group': group,
                'last_keywords': keywords,
                'last_sentiment': sentiment,
                'last_timestamp': timestamp
            })

        # Group profile/activity
        if group:
            if not hasattr(self, 'group_memory'):
                self.group_memory = {}
            group_profile = self.group_memory.setdefault(group, {})
            group_profile['last_message'] = text
            group_profile['last_user'] = user
            group_profile['last_keywords'] = keywords
            group_profile['last_sentiment'] = sentiment
            group_profile['last_wallets'] = wallets
            group_profile['last_tokens'] = tokens
            group_profile['last_timestamp'] = timestamp

        # Keyword tracking
        if not hasattr(self, 'keyword_store'):
            self.keyword_store = {}
        for kw in keywords:
            kw_data = self.keyword_store.setdefault(kw, {'count': 0, 'last_context': []})
            kw_data['count'] += 1
            kw_data['last_context'].append({'group': group, 'user': user, 'text': text, 'timestamp': timestamp})
            if len(kw_data['last_context']) > 10:
                kw_data['last_context'] = kw_data['last_context'][-10:]

        # Wallet tracking
        for wallet in wallets:
            self.wallet.register_wallet_intel(wallet, {'traits': {'telegram_mentioned'}, 'last_seen': timestamp})
    """
    One librarian to rule them all. Central ingestion, normalization, and indexing.
    """

    def __init__(self):
        self.stream = LibrarianStream()
        self.token = LibrarianToken()
        self.wallet = LibrarianWallet()
        self.nlp = LibrarianNLP()
        self.telegram = LibrarianTelegram()
    
    def ingest_telegram_user(self, user_id: str, profile: Dict[str, Any]):
        """
        Ingest or update a Telegram user's profile.
        """
        self.telegram.ingest_user_profile(user_id, profile)

    def update_telegram_activity(self, user_id: str, activity: Dict[str, Any]):
        """
        Update Telegram user activity log.
        """
        self.telegram.update_activity(user_id, activity)

    def score_telegram_user(self, user_id: str) -> float:
        """
        Get the score for a Telegram user.
        """
        return self.telegram.score_user(user_id)

    def get_telegram_user_profile(self, user_id: str) -> Dict[str, Any]:
        """
        Get the profile for a Telegram user.
        """
        return self.telegram.get_user_profile(user_id)
        self.persistence_dir = "/home/ubuntu/nyx/runtime/library/"
        self.token_memory = {}
        self.wallet_memory = {}
        self.group_memory = {}
        self.trade_feedback = []
        self.strategy_memory = {}
        self._memory_store = {}
        self._access_log: Dict[str, dict] = {}
        self._memory_ttl: Dict[str, float] = {}
        self._registered_objects = {}
        self._memory_file = os.path.expanduser("/home/ubuntu/nyx/runtime/memory/librarian.json")
        self._memory_loaded = False
        self.counters = {"events_ingested": 0, "stream_events": 0}
        self._file_offsets: Dict[Path, int] = {}
        self._bus = None
        self._lock = asyncio.Lock()
        self._last_status_beat = 0.0
        self.seen_tokens: Dict[str, dict] = self._load_json("seen_tokens.json")
        self.seen_x_posts: Dict[str, dict] = self._load_json("seen_x_posts.json")
        self.seen_wallets: Dict[str, dict] = self._load_json("seen_wallets.json")
        self.token_profiles: Dict[str, dict] = self._load_json("token_profiles.json")
        self.seen_token_names: dict
        self.seen_x_posts_by_name: dict
        self._skip_no_contract_count = 0
        self._skip_last_warn_ts = 0.0
        self._skip_warn_every_n = 50
        self._skip_warn_min_s = 60.0
        self._skip_sampled = 0
        self._skip_sample_cap = 5
        self._skip_sample_window_start = 0.0
        self._skipped_samples_path = "/home/ubuntu/nyx/runtime/monitor/skipped_stream_samples.jsonl"




    def _ensure_maps(self):
        if not hasattr(self, "token_tags"):   self.token_tags = {}
        if not hasattr(self, "wallet_tags"):  self.wallet_tags = {}
        if not hasattr(self, "seen_tokens"):  self.seen_tokens = {}
        if not hasattr(self, "seen_wallets"): self.seen_wallets = {}
        if not hasattr(self, "seen_x_posts"): self.seen_x_posts = {}

    def _save_json(self, obj: Any, filename: str):
        import json, os
        os.makedirs(self.runtime_dir, exist_ok=True)
        path = os.path.join(self.runtime_dir, filename)
        try:
            with open(path, "w") as f:
                json.dump(obj, f, indent=2)
        except Exception:
            pass

    def tag_token(self, token: str, tag: str):
        """
        Lightweight tagging used by many call sites.
        Persists to runtime json: token_tags.json
        """
        if not token or not tag:
            return
        self._ensure_maps()
        tags = self.token_tags.get(token)
        if tags is None:
            tags = set()
            self.token_tags[token] = tags
        tags.add(tag)
        serializable = {k: sorted(list(v)) for k, v in self.token_tags.items()}
        self._save_json(serializable, "token_tags.json")

    def tag_wallet(self, wallet: str, tag: str):
        """
        Lightweight wallet tagging.
        Persists to runtime json: wallet_tags.json
        """
        if not wallet or not tag:
            return
        self._ensure_maps()
        tags = self.wallet_tags.get(wallet)
        if tags is None:
            tags = set()
            self.wallet_tags[wallet] = tags
        tags.add(tag)
        serializable = {k: sorted(list(v)) for k, v in self.wallet_tags.items()}
        self._save_json(serializable, "wallet_tags.json")

    def record_signal(self, payload: dict):
        """
        Append an event/trace row into a rolling jsonl file.
        """
        import json, os, time
        os.makedirs(self.runtime_dir, exist_ok=True)
        path = os.path.join(self.runtime_dir, "signals.jsonl")
        try:
            payload = dict(payload or {})
            payload.setdefault("ts", time.time())
            with open(path, "a") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:

    def attach_bus(self, bus):
        """
        Attach signal_bus so we mirror its events too (optional).
        """
        self._bus = bus
        async def _mirror(payload):
            await self.record_event("bus_signal", payload)
        self._bus_register_helper = _mirror

    @property
    def memory_store(self) -> dict:
        """Back-compat for modules that used librarian.memory_store"""
        return self._memory_store

    @memory_store.setter
    def memory_store(self, value: dict) -> None:
        if isinstance(value, dict):
            self._memory_store.update(value)
        candidate_mints.update(wallet_buys.keys())
        candidate_mints.update(liq_added)

        def get_ohlcv(mint: str, limit: int = 60):
            try:
                return self.get_ohlcv(mint, limit=limit)
            except Exception:
                return []

        def volume_spike(candles, lookback=30, k=2.0) -> bool:
            if not candles or len(candles) < lookback + 1:
                return False
            vols = [float(c.get("volume", 0)) for c in candles[-(lookback+1):]]
            recent = vols[-1]
            base = sum(vols[:-1]) / max(1, len(vols[:-1]))
            return recent > k * base if base > 0 else False

        ranked: list[tuple[str, float]] = []
        for mint in candidate_mints:
            s_count = social_counts.get(mint, 0)
            w_count = wallet_buys.get(mint, 0)
            st_count = stream_counts.get(mint, 0)
            liq_boost = 1.0 if mint in liq_added else 0.0
            spike_boost = 1.0 if volume_spike(get_ohlcv(mint)) else 0.0

            score = (
                (1.0 if s_count >= min_social_mentions else 0.0)
                + (1.0 if w_count >= min_wallet_buys else 0.0)
                + (0.5 if st_count >= 5 else 0.0)
                + liq_boost
                + spike_boost
            )
            if score > 0.0:
                ranked.append((mint, score))

        ranked.sort(
            key=lambda t: (t[1],
                           wallet_buys.get(t[0], 0),
                           social_counts.get(t[0], 0),
                           stream_counts.get(t[0], 0)),
            reverse=True,
        )
        return [m for (m, _s) in ranked[:max(1, int(limit))]]

    def save_memory(self):
        """
        Persist in-memory state to disk and record a journal entry.
        Safe and idempotent. Uses atomic write.
        """
        try:
            entry = {
                "ts": datetime.utcnow().isoformat(),
                "summary": {
                    "keys": list(self._memory_store.keys()),
                    "counts": {
                        k: (len(v) if isinstance(v, (list, dict, set, tuple)) else 1)
                        for k, v in self._memory_store.items()
                    }
                }
            }

            try:
                if hasattr(self, "_list_append"):
                    self._list_append("events", entry)
                else:
                    buf = self._memory_store.setdefault("events", [])
                    if isinstance(buf, list):
                        buf.append(entry)
            except Exception:
                pass

            try:
                from librarian.rules import telegram_auto_track
                if hasattr(telegram_auto_track, "run_on_memory_entry"):
                    telegram_auto_track.run_on_memory_entry(entry)
            except Exception:
                pass

            tmp = f"{self._memory_file}.tmp"
            with open(tmp, "w") as f:
                json.dump(self._memory_store, f, indent=2, default=str)
            os.replace(tmp, self._memory_file)

            logging.debug("🧠 Librarian: Saved runtime memory.")
        except Exception as e:
            logging.warning(f"[Librarian] Failed to save memory: {e}")

    def remember(self, key: str, value):
        self._memory_store[key] = value
        self.save_memory()

    def recall(self, key: str, default=None):
        return self._memory_store.get(key, default)

    def remember_list(self, key: str, value):
        self._memory_store.setdefault(key, [])
        if value not in self._memory_store[key]:
            self._memory_store[key].append(value)
            self.save_memory()

    def decay_keywords(self, decay_rate: float = 0.9, min_weight: float = 0.1):
        """
        Gradually decays stored keyword weights to prevent stale bias.
        decay_rate: Multiplier applied to each keyword's weight (default 0.9 = 10% decay).
        min_weight: Keywords below this weight will be removed entirely.
        """
        try:
            keyword_store = getattr(self, "keyword_store", {})

            if not isinstance(keyword_store, dict):
                return

            to_delete = []
            for keyword, weight in keyword_store.items():
                try:
                    new_weight = weight * decay_rate
                    if new_weight < min_weight:
                        to_delete.append(keyword)
                    else:
                        keyword_store[keyword] = new_weight
                except Exception:
                    continue

            for keyword in to_delete:
                del keyword_store[keyword]

        except Exception as e:
            import logging
            logging.warning(f"[DataLibrarian] Keyword decay failed: {e}")

    def ingest_token_profile(self, profile: dict):
        """
        Delegate to LibrarianToken for token profile ingestion.
        """
        self.token.ingest_token_profile(profile)

    def _enrich_token_profile(self, contract: str):
        """
        Reconstructs missing fields in a token profile from other known data.
        Fills in name, symbol, source, theme, tags, and wallet overlap.
        """
        try:
            token = self.seen_tokens.get(contract)
            if not token:
                return

            if not token.get("name"):
                token["name"] = self.token_name_map.get(contract) or "unknown"
            if not token.get("symbol"):
                token["symbol"] = token.get("name", "???")[:4].upper()

            name_lower = token.get("name", "").lower()
            matched = [
                theme for theme in self.theme_keywords
                if theme in name_lower
            ]
            token["theme"] = matched or []

            overlap_tags = []
            for addr in token.get("wallets", []):
                wallet_info = self.wallet_memory.get(addr, {})
                wallet_tags = wallet_info.get("tags", [])
                overlap_tags.extend(wallet_tags)

            token["wallet_tags"] = list(set(overlap_tags))

            self.seen_tokens[contract] = token
            self._save_json(self.seen_tokens, "seen_tokens.json")

        except Exception as e:
            logging.warning(f"[Librarian] Failed to enrich token profile: {e}")

    def has_seen_token(self, contract: str) -> bool:
        """
        Returns True if the token contract has been seen before in the system.
        """
        return contract in self.seen_tokens

    def has_seen_x(self, handle_or_id: str) -> bool:
        """
        Returns True if this X (Twitter) account has been logged or tracked before.
        Accepts either the handle (@name) or numeric ID.
        """
        return handle_or_id in self.x_memory or handle_or_id.lower() in self.x_memory

    def register_wallet_intel(self, wallet: str, traits: Optional[Dict] = None):
        """
        Delegate to LibrarianWallet for wallet intel registration.
        """
        self.wallet.register_wallet_intel(wallet, traits)

    def register_x_alpha(self, handle: str, token: str = None, reason: str = None):
        """
        Delegate to LibrarianNLP for X alpha registration.
        """
        self.nlp.register_x_alpha(handle, token, reason)

    async def get_group_map(self, key: str):
        """
        Retrieve a stored group map by key.
        Returns a dict if found, or None if missing.
        """
        try:
            store = getattr(self, "memory_store", {})
            if isinstance(store, dict):
                value = store.get(key)
                if value and isinstance(value, dict):
                    return value
        except Exception as e:
            import logging
            logging.warning(f"[DataLibrarian] Failed to get group map for '{key}': {e}")
        return None

    def get_tokens_in_wallet(self, wallet_address: str):
        """
        Return a list of tokens currently associated with a wallet.
        """
        try:
            wallet_data = getattr(self, "wallet_store", {})
            if isinstance(wallet_data, dict):
                return wallet_data.get(wallet_address, [])
        except Exception as e:
            import logging
            logging.warning(f"[DataLibrarian] Failed to get tokens in wallet {wallet_address}: {e}")
        return []

    def clear_memory_logs(self, max_age_days: int = 7, max_token_count: int = 2000):
        trimmed = 0
        now = datetime.utcnow()
        cutoff = now - timedelta(days=max_age_days)

        token_mem = self.memory("token_memory", {})
        if not isinstance(token_mem, dict):
            raise TypeError("token_memory is not a dict")

        new_token_memory = {}
        for token, data in token_mem.items():
            last_seen = data.get("last_seen")
            if not last_seen:
                continue
            try:
                timestamp = datetime.fromisoformat(last_seen)
                if timestamp > cutoff:
                    new_token_memory[token] = data
                else:
                    trimmed += 1
            except Exception:
                new_token_memory[token] = data

        token_trimmed = dict(
            sorted(new_token_memory.items(), key=lambda x: x[1].get("last_seen", ""), reverse=True)[:max_token_count]
        )
        self.set_memory("token_memory", token_trimmed)

        strategy_mem = self.get_memory("strategy_memory", {})
        if isinstance(strategy_mem, dict) and len(strategy_mem) > 3000:
            self.set_memory("strategy_memory", dict(list(strategy_mem.items())[-2000:]))
            trimmed += 1

        logging.info(f"[Librarian] 🧠 Trimmed {trimmed} old token logs")
        return f"🧠 Trimmed {trimmed} old memory entries."

    def prune_memory(self, max_items_per_key: int = 500, max_age_days: int = 14) -> int:
        """
        Trim oversized lists and drop entries older than max_age_days.
        Returns number of entries pruned.
        """
        pruned = 0
        store = getattr(self, "_memory_store", {})
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)

        for key, val in list(store.items()):
            if isinstance(val, list):
                if len(val) > max_items_per_key:
                    del_count = len(val) - max_items_per_key
                    store[key] = val[-max_items_per_key:]
                    pruned += del_count

                new_list = []
                for item in store[key]:
                    ts = None
                    if isinstance(item, dict):
                        ts = item.get("ts") or item.get("timestamp")
                    try:
                        if ts:
                            dt = datetime.fromisoformat(str(ts).replace("Z",""))
                            if dt < cutoff:
                                pruned += 1
                                continue
                    except Exception:
                        pass
                    new_list.append(item)
                store[key] = new_list

            if isinstance(val, dict) and not val:
                continue

        self._memory_store = store
        return pruned

    def _first_base58(self, *args):
        for f in args:
            if isinstance(f, str):
                m = _BASE58_RE.search(f)
                if m:
                    return m.group(0)
            elif isinstance(f, (list, tuple)):
                for x in f:
                    m = _BASE58_RE.search(str(x))
                    if m:
                        return m.group(0)
            elif isinstance(f, dict):
                for v in f.values():
                    m = _BASE58_RE.search(str(v))
                    if m:
                        return m.group(0)
        return None

    def _pick_contract(self, event: dict) -> tuple[str | None, str | None]:
        """
        Best-effort extraction of (contract_mint, token_name_or_symbol).
        Rule order:
          1) Explicit fields on the event: contract/mint/token/program_id
          2) Any base58 address in tokens[], wallets[], logs, signature
          3) Name/symbol from event or tokens[]
        Returns (contract, token_name). Either may be None.
        """
        if not isinstance(event, dict):
            return None, None

        contract = (
            event.get("contract")
            or event.get("mint")
            or event.get("token")
            or event.get("program_id")
        )
        if not (isinstance(contract, str) and _BASE58_RE.fullmatch(contract)):
            contract = self._first_base58(
                contract,
                event.get("signature"),
                event.get("program_id"),
                event.get("tokens"),
                event.get("wallets"),
                event.get("logs"),
                event.get("raw"),
            )

        token_name = (
            event.get("token_name")
            or event.get("symbol")
            or event.get("name")
        )

        tokens = event.get("tokens") or []
        for t in tokens:
            if isinstance(t, dict):
                token_name = token_name or t.get("symbol") or t.get("name")
                if not contract:
                    contract = self._first_base58(t)

        return contract, token_name

    async def ingest_stream_event(self, event: dict):
        """
        Catalog and classify streamed Solana log data.
        Tags tokens/wallets, stores signal metadata, X links, and raw trace.
        Requires either contract (mint) OR token_name to proceed.
        """
        try:
            contract, token_name = self._pick_contract(event)

            if not (contract or token_name):
                logging.warning("[Librarian] Skipped stream event with no contract or token name")
                return

            token_list  = event.get("tokens", []) or []
            wallet_list = event.get("wallets", []) or []
            wallet      = event.get("wallet")
            kind        = event.get("kind", "unknown")
            source      = event.get("source", "solana_stream")
            x_data      = event.get("x_meta")
            signature   = event.get("signature")
            program_id  = event.get("program_id")
            slot        = event.get("slot")
            timestamp   = event.get("timestamp") or time.time()
            logs        = event.get("logs")

            for t in token_list:
                if isinstance(t, str):
                    self.tag_token(t, "stream_seen")
                elif isinstance(t, dict):
                    mint = t.get("mint") or t.get("address") or t.get("token") or t.get("contract")
                    if mint:
                        self.tag_token(mint, "stream_seen")

            for w in wallet_list:
                if isinstance(w, str):
                    self.tag_wallet(w, "stream_seen")
                elif isinstance(w, dict):
                    addr = w.get("address") or w.get("wallet")
                    if addr:
                        self.tag_wallet(addr, "stream_seen")

            key = contract or token_name
            self.seen_tokens[key] = {
                "seen_at": time.time(),
                "kind": kind,
                "source": source,
                "wallet": wallet,
                "token_name": token_name,
                "contract": contract,
                "x_data": x_data,
                "raw": event,
            }
            self._save_json(self.seen_tokens, "seen_tokens.json")

            if wallet:
                self.seen_wallets[wallet] = {
                    "contract": contract,
                    "token_name": token_name,
                    "first_seen": time.time(),
                    "event_type": kind,
                }
                self._save_json(self.seen_wallets, "seen_wallets.json")

            if x_data:
                self.seen_x_posts[key] = {
                    "timestamp": time.time(),
                    "keywords": x_data.get("keywords", []) or [],
                    "poster": x_data.get("poster"),
                    "x_text": x_data.get("text", ""),
                }
                self._save_json(self.seen_x_posts, "seen_x_posts.json")

                kw = [k for k in (x_data.get("keywords") or []) if isinstance(k, str)]
                if kw:
                    try:
                        from strategy.strategy_memory import update_meta_keywords
                        update_meta_keywords(token_address=(contract or token_name), keywords=kw)
                    except Exception:
                        pass

            self.record_signal({
                "source": source,
                "signature": signature,
                "program_id": program_id,
                "slot": slot,
                "wallets": wallet_list,
                "tokens": token_list,
                "timestamp": timestamp,
                "logs": logs,
                "contract": contract,
                "token_name": token_name,
            })

            if contract:
                with contextlib.suppress(Exception):
                    self._enrich_token_profile(contract)

        except Exception as e:
            logging.warning(f"[Librarian] Error ingesting stream event: {e}")

    async def get_token(self, token: str) -> Optional[TokenRecord]:
        async with self._lock:
            return self._tokens.get(token)

    async def get_wallet(self, wallet: str) -> Optional[WalletRecord]:
        async with self._lock:
            return self._wallets.get(wallet)

    async def get_top_tokens(self, limit: int = 20, by: str = "activity") -> List[TokenRecord]:
        async with self._lock:
            if by == "activity":
                items = sorted(self._tokens.values(), key=lambda x: x.last_ts, reverse=True)
            elif by == "score":
                items = sorted(
                    self._tokens.values(),
                    key=lambda x: (x.scores[-1]["final_score"] if x.scores else 0),
                    reverse=True,
                )
            else:
                items = sorted(self._tokens.values(), key=lambda x: x.last_ts, reverse=True)
            return items[:limit]

    async def get_active_wallets(self, limit: int = 20) -> List[WalletRecord]:
        async with self._lock:
            items = sorted(self._wallets.values(), key=lambda x: x.last_ts, reverse=True)
            return items[:limit]

    async def get_signals_for_token(self, token: str, limit: int = 100) -> List[dict]:
        """Return mixed signals/events indexed for a given token."""
        async with self._lock:
            rec = self._tokens.get(token)
            if not rec:
                return []
            return list(rec.events)[-limit:]

    async def get_signals_for_wallet(self, wallet: str, limit: int = 100) -> List[dict]:
        async with self._lock:
            rec = self._wallets.get(wallet)
            if not rec:
                return []
            return list(rec.events)[-limit:]

    async def stats(self) -> dict:
        async with self._lock:
            return {
                "tokens": len(self._tokens),
                "wallets": len(self._wallets),
                "events_by_type": {k: len(v) for k, v in self._events_by_type.items()},
                "tracked_files": len(self._file_offsets),
                "last_status_beat": self._last_status_beat,
            }

    def trim_token_history(self, max_entries: int = 500, max_age_days: int = None):
        """
        Trim token history by max entries or age.
        - If max_entries is set, keep only that many recent events.
        - If max_age_days is set, remove events older than that.
        """
        try:
            history_store = getattr(self, "token_history_store", {})
            if not isinstance(history_store, dict):
                return

            now = datetime.utcnow().timestamp()
            max_age_seconds = max_age_days * 86400 if max_age_days else None

            for token, events in history_store.items():
                if max_age_seconds:
                    events = [e for e in events if isinstance(e, dict) and now - e.get("timestamp", now) <= max_age_seconds]

                if isinstance(events, list) and len(events) > max_entries:
                    events = events[-max_entries:]

                history_store[token] = events

        except Exception as e:
            import logging
            logging.warning(f"[DataLibrarian] Failed to trim token history: {e}")

    def _index_token_event(self, token: str, ev: dict):
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

    def _index_wallet_event(self, wallet: str, ev: dict):
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

    def setup_delegates(self):
        self.chat = self.LibrarianChat(self.runtime)
        self.maintenance = self.LibrarianMaintenance(self)

    async def ingest_chat_messages(self, messages: list[dict]) -> None:
        await self.chat.ingest_chat_messages(messages)

    async def ingest_records(self, kind: str, records: list[dict]) -> None:
        await self.chat.ingest_records(kind, records)

    async def build_context(self, token: str) -> dict:
        """
        Build a rich context dictionary about a token for scoring, evaluation, or analysis.

        Pulls from:
        - token memory
        - wallet traits
        - social memory
        - tag and flag systems
        - persisted recall cache
        """
        from utils.token_utils import normalize_token_address
        token = normalize_token_address(token)

        context = {
            "token": token,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": {},
            "chart": {},
            "wallets": {},
            "volume": {},
            "social": {},
            "nft": {},
            "price": 0.0,
            "tags": [],
            "risk_flags": [],
            "flags": [],
            "wallet_traits": [],
            "x_flags": [],
            "score": 0,
            "meta_theme": None,
            "created": None
        }

        try:
            saved = recall(f"token:{token}", default={})

            context["metadata"]     = saved.get("metadata", {})
            context["tags"]         = saved.get("tags", [])
            context["chart"]        = saved.get("chart_data", {})
            context["wallets"]      = saved.get("wallets", {})
            context["social"]       = saved.get("social", {})
            context["volume"]       = saved.get("volume", {})
            context["nft"]          = saved.get("nft", {})
            context["risk_flags"]   = saved.get("risk_flags", [])

            token_data = self.token_memory.get(token, {})
            associated_wallets = token_data.get("wallets", [])
            associated_x        = token_data.get("x_mentions", [])

            wallet_traits = set()
            for w in associated_wallets:
                wallet_info = self.wallet_memory.get(w, {})
                wallet_traits.update(wallet_info.get("traits", []))

            x_flags = set()
            for x in associated_x:
                x_info = self.x_memory.get(x.lower(), {})
                x_flags.update(x_info.get("reasons", []))

            context["flags"]         = list(token_data.get("flags", []))
            context["wallet_traits"] = list(wallet_traits)
            context["x_flags"]       = list(x_flags)
            context["score"]         = token_data.get("score", 0)
            context["meta_theme"]    = token_data.get("meta_theme", None)
            context["created"]       = token_data.get("created", None)

            log_event(f"📚 Context built for token {token}")

        except Exception as e:
            logging.warning(f"[Librarian] Failed to build context for {token}: {e}")

        return self.enrich_context_with_extras(context)



def _ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def _library_path_for(genre: str, ts: float) -> Path:
    dt = datetime.utcfromtimestamp(ts)
    base = LIBRARY_ROOT / genre / f"{dt.year:04d}" / f"{dt.month:02d}"
    _ensure_dir(base)
    return base / f"{dt.day:02d}.jsonl"

async def archive_to_library(self, ev: dict):
    """
    Normalize, classify, write to /runtime/library/<genre>/YYYY/MM/DD.jsonl
    Also updates in-memory indices for fast queries.
    """
    try:
        ts = ev.get("ts") or ev.get("timestamp") or datetime.utcnow().timestamp()
        etype = ev.get("type") or ev.get("payload", {}).get("type") or "event"
        payload = ev.get("payload") or ev

        token = payload.get("token") or payload.get("mint") or payload.get("token_address")
        wallet = payload.get("wallet") or payload.get("wallet_address") or payload.get("owner")

        genre = _classify_genre(payload)
        topics = sorted(list(_extract_topics(payload)))

        line = {
            "ts": ts,
            "type": etype,
            "genre": genre,
            "topics": topics,
            "token": token,
            "wallet": wallet,
            "payload": payload,
        }

        fpath = _library_path_for(genre, ts)
        with open(fpath, "a") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

        lib_ix = self._memory_store.setdefault("_library_index", {
            "by_genre": {},
            "by_topic": {},
            "by_token": {},
            "by_wallet": {},
            "wallet_class": {},
        })

        arr = lib_ix["by_genre"].setdefault(genre, [])
        arr.append({"ts": ts, "type": etype, "token": token, "wallet": wallet, "topics": topics})
        if len(arr) > 5000: del arr[: len(arr) - 5000]

        for t in topics:
            ta = lib_ix["by_topic"].setdefault(t, [])
            ta.append({"ts": ts, "type": etype, "token": token, "wallet": wallet, "genre": genre})
            if len(ta) > 5000: del ta[: len(ta) - 5000]

        if token:
            tt = lib_ix["by_token"].setdefault(token, [])
            tt.append({"ts": ts, "type": etype, "genre": genre, "topics": topics, "wallet": wallet})
            if len(tt) > 5000: del tt[: len(tt) - 5000]

        if wallet:
            ww = lib_ix["by_wallet"].setdefault(wallet, [])
            ww.append({"ts": ts, "type": etype, "genre": genre, "topics": topics, "token": token})
            if len(ww) > 5000: del ww[: len(ww) - 5000]

        if genre == "profits" and wallet:
            if lib_ix["wallet_class"].get(wallet) != "bad":
                lib_ix["wallet_class"][wallet] = "good"
        if genre == "losses" and wallet:
            if lib_ix["wallet_class"].get(wallet) != "good":
                lib_ix["wallet_class"][wallet] = "bad"

        if int(ts) % 300 == 0:
            self.save_memory()

    except Exception as e:
        import logging
        logging.warning(f"[Librarian] archive_to_library error: {e}")

def query_by_genre(self, genre: str, limit: int = 200) -> list:
    idx = self._memory_store.get("_library_index", {}).get("by_genre", {}).get(genre, [])
    return idx[-limit:]

def query_by_topic(self, topic: str, limit: int = 200) -> list:
    idx = self._memory_store.get("_library_index", {}).get("by_topic", {}).get(topic, [])
    return idx[-limit:]

def get_wallet_class(self, wallet: str) -> str:
    return self._memory_store.get("_library_index", {}).get("wallet_class", {}).get(wallet, "")



librarian = DataLibrarian()

async def run_librarian():
    await librarian.start()

