from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.live_config import config
from core.llm.group_message_embedder import embed_group_message
from core.llm.lexicon_tracker import lexicon_tracker
from cortex.meta_cortex import score_token_with_meta
from inputs.meta_data.token_metadata import is_dust_token, parse_token_metadata
from inputs.social.group_reputation import get_group_score, update_group_score
from inputs.social.sentiment_scanner import extract_keywords_and_sentiment
from inputs.social.telegram_clients import ensure_user_client_started
from inputs.social.token_extraction import extract_tokens_from_text
from memory.telegram_memory_index import get_recent_group_messages
from special.insight_logger import log_ai_insight
from strategy.strategy_memory import is_blacklisted_token, tag_token_result
from telethon import events
from utils.clean_text import clean_message_text
from utils.crash_guardian import crash_guardian
from utils.logger import log_event
from utils.service_status import update_status
from utils.token_mention_logger import log_token_mention
from utils.universal_input_validator import ensure_dict, ensure_str, validate_token_record
from cortex.core_router import handle_event
from utils.token_utils import mint_address_or_best_guess, is_solana_address

# === Realtime Queue of Group Mentions ===
group_mentions_queue: asyncio.Queue = asyncio.Queue()
group_mentions_seen: set[str] = set()

REPUTATION_THRESHOLDS = {
    "trusted": 10,
    "neutral": 0,
    "flagged": -5
}

POLL_INTERVAL = 0.25

# ---------- safe logging helpers ----------
def _s(v):
    return "" if v is None else str(v)

def _safe_line(group, symbol, text):
    return f"[TG] { _s(group) }: { _s(symbol) } { _s(text) }"

async def _maybe_call(func, *args, **kwargs):
    """Call func whether it's sync or async; always await if needed."""
    try:
        if func is None:
            return None
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        res = func(*args, **kwargs)
        if inspect.isawaitable(res):
            return await res
        return res
    except Exception as e:
        logging.warning(f"[TGNLP] call failed: {e}")
        return None


class TelegramGroupListener:
    def __init__(self):
        self.executor = None
        self.running = False
        self.enabled = config.get("telegram_learning_enabled", True)
        self.allow_talk = config.get("telegram_talking_enabled", True)
        self._handler_bound = False  # avoid double-binding handlers

    async def run(self):
        """
        Starts the Telethon user client handler (once) and processes the queue.
        """
        update_status("telegram_group_listener")
        client = await ensure_user_client_started()

        if not self._handler_bound:
            @client.on(events.NewMessage)
            async def handle_group_message(event):
                try:
                    # Only process group/supergroup chats
                    chat = await event.get_chat()
                    if not getattr(chat, "megagroup", False) and getattr(chat, "title", None) is None:
                        return
                    if not self.enabled:
                        return
                    await self.process_event_message(event)
                except Exception as e:
                    logging.warning(f"[TG Listener] Failed to process: {e}")
            self._handler_bound = True
            log_event("[TG Listener] User client handler attached.")

        self.running = True
        while self.running:
            try:
                crash_guardian.beat("TGUserSignalListener")
                await self.process_next_mention()
            except Exception as e:
                log_event(f"[TelegramGroupListener] Error: {e}")
            await asyncio.sleep(POLL_INTERVAL)

    async def _get_executor(self):
        if self.executor is None:
            from exec.trade_executor import TradeExecutor
            self.executor = TradeExecutor()
        return self.executor

    async def process_event_message(self, event):
        """
        Process a Telethon NewMessage event.
        """
        chat = await event.get_chat()
        group_name = ensure_str(getattr(chat, "title", "") or getattr(chat, "username", "") or "Unnamed Group")
        text = clean_message_text(ensure_str(getattr(event, "raw_text", "") or getattr(event.message, "message", "")))

        sender_ent = await event.get_sender()
        sender = (
            (ensure_str(getattr(sender_ent, "first_name", "")) + " " + ensure_str(getattr(sender_ent, "last_name", ""))).strip()
            or ensure_str(getattr(sender_ent, "username", ""))
            or "Unknown"
        )

        if not text or len(text) < 4:
            return

        # allow/block lists
        allowlist = config.get("group_allowlist", [])
        blocklist = config.get("group_blocklist", [])
        if blocklist and any(x.lower() in group_name.lower() for x in blocklist):
            return
        if allowlist and not any(x.lower() in group_name.lower() for x in allowlist):
            return

        # quick lexicon learn (best-effort)
        try:
            lexicon_tracker().add_from_text(text, source=group_name, context="chat_signal")
        except Exception:
            pass

        # NLP (handle sync or async implementation)
        try:
            kws_and_sent = await _maybe_call(extract_keywords_and_sentiment, text)
            if isinstance(kws_and_sent, tuple) and len(kws_and_sent) >= 2:
                keywords, sentiment_score = kws_and_sent[0], kws_and_sent[1]
            else:
                keywords, sentiment_score = [], None
        except Exception as e:
            logging.warning(f"[TGNLP] Failed telegram NLP: {e}")
            keywords, sentiment_score = [], None

        tagged_wallets = self.detect_wallets(text)

        # log nicely without NoneType issues
        logging.info(_safe_line(group_name, None, text))
        log_event(f"🧠 Keywords: {keywords} | Sentiment: {sentiment_score}")

        # embed for memory/LLM (NOTE: embed_group_message expects text=...)
        try:
            await embed_group_message(
                text=text,
                group_name=group_name,
                timestamp=datetime.utcnow().isoformat(),
                keywords=keywords,
                sentiment=sentiment_score if isinstance(sentiment_score, (int, float)) else None,
                sender=sender,
                wallets=tagged_wallets,
            )
        except Exception as e:
            logging.warning(f"[TG Listener] embed_group_message failed: {e}")

        # token mentions → queue for scoring/buy (and log junk)
        try:
            # raw candidates
            token_candidates = extract_tokens_from_text(text)
            for candidate in token_candidates:
                # Always log the observation
                log_token_mention(
                    group=group_name,
                    message=text,
                    symbol=None,            # fill if you parse $TICKER separately
                    mint=candidate,
                    status="observed",
                )

                # Try to normalize/validate for the safe pipeline
                rec = validate_token_record({
                    "group": group_name,
                    "symbol": None,
                    "message": text,
                    "mint": candidate,
                    "timestamp": datetime.utcnow().isoformat(),
                })

                if not rec:
                    log_token_mention(
                        group=group_name,
                        message=text,
                        symbol=None,
                        mint=candidate,
                        status="no_mint",
                        reason="validator:missing_mint",
                    )
                    continue

                add_group_mention(group_name, rec["mint"])
                log_token_mention(
                    group=group_name,
                    message=text,
                    symbol=rec.get("symbol"),
                    mint=rec["mint"],
                    status="queued",
                    reason="validator:ok",
                )
                await handle_event({
                    "token": mint_address_or_best_guess,   # prefer mint if you have it
                    "action": "social_update",
                    "messages": [{"text": text, "group": group_name, "ts": datetime.utcnow().isoformat()}],
                    "source": "telegram_group",
                })
        except Exception as e:
            logging.warning(f"[TG Listener] token extraction failed: {e}")

        # optional talk-back
        if self.allow_talk and self.should_respond(text):
            await self.respond_to_event(event, text)

        # activity tracker
        self.track_group_activity(group_name)

    async def process_next_mention(self):
        try:
            group, token_address = await asyncio.wait_for(group_mentions_queue.get(), timeout=5)
        except asyncio.TimeoutError:
            return

        key = f"{group}:{token_address}"
        if key in group_mentions_seen:
            return
        group_mentions_seen.add(key)

        if is_blacklisted_token(token_address):
            return

        try:
            metadata = await parse_token_metadata(token_address)
            metadata = ensure_dict(metadata)
        except Exception as e:
            log_event(f"[TG Listener] Metadata fetch failed for {token_address}: {e}")
            return

        if not metadata or is_dust_token(metadata):
            return

        group_score = get_group_score(group)
        score_modifier = (
            5 if group_score >= REPUTATION_THRESHOLDS["trusted"] else
            -3 if group_score <= REPUTATION_THRESHOLDS["flagged"] else 0
        )

        try:
            score = await score_token_with_meta(
                token_address=token_address,
                metadata=metadata,
                source="telegram_group",
                detected_time=time.time(),
                custom_boost=score_modifier,
            )
        except Exception as e:
            log_event(f"[TG Listener] MetaCortex score failed for {token_address}: {e}")
            return

        if score >= config.get("min_confidence_score", 70):
            log_event(f"📣 Group Signal: {group} tagged {metadata.get('symbol', '?')} ({token_address}) with score {score}")
            try:
                executor = await self._get_executor()
                result = await executor.buy_token(token_address, metadata, score=score, source="telegram_group")
            except Exception as e:
                log_event(f"[TG Listener] buy_token failed: {e}")
                return

            if isinstance(result, dict) and result.get("status") == "success":
                try:
                    tag_token_result(token_address, "group_signal")
                except Exception:
                    pass
                try:
                    update_group_score(group, result.get("outcome", "profit"))
                except Exception:
                    pass
                try:
                    log_ai_insight("group_signal_buy", {
                        "group": group,
                        "token": token_address,
                        "score": score,
                        "symbol": metadata.get("symbol"),
                        "confidence_boost": score_modifier
                    })
                except Exception:
                    pass

    def should_respond(self, text: str) -> bool:
        text = text.lower()
        triggers = ["nyx", "bot", "what do you think", "any alpha", "help", "thoughts"]
        return any(trigger in text for trigger in triggers)

    async def respond_to_event(self, event, cleaned_text: str):
        try:
            from core.llm.llm_brain import llm_brain
            response = await llm_brain.reply_to_group(cleaned_text, event.chat.id)
            if response:
                await event.reply(response)
        except Exception as e:
            log_event(f"[TG Talk] Response failed: {e}")

    def detect_wallets(self, text: str) -> list:
        pattern = r"([1-9A-HJ-NP-Za-km-z]{32,44})"
        return re.findall(pattern, text)

    def track_group_activity(self, group_name: str):
        try:
            path = os.path.expanduser("~/nyx/runtime/logs/group_activity.json")
            data = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    try:
                        data = json.load(f)
                    except Exception:
                        data = {}
            data[group_name] = datetime.utcnow().isoformat()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logging.warning(f"[GroupTracker] Error saving activity: {e}")

    def prune_idle_groups(self) -> List[str]:
        path = os.path.expanduser("~/nyx/runtime/logs/group_activity.json")
        threshold_days = 30
        now = datetime.utcnow()
        left: List[str] = []

        if not os.path.exists(path):
            return left

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                try:
                    data = json.load(f)
                except Exception:
                    return left

            if not isinstance(data, dict):
                return left

            for group_name, last_seen in list(data.items()):
                try:
                    last_time = datetime.fromisoformat(last_seen)
                    days_inactive = (now - last_time).days
                    if days_inactive >= threshold_days:
                        left.append(group_name)
                        del data[group_name]
                        log_event(f"👋 Leaving inactive group: {group_name} (inactive {days_inactive} days)")
                except Exception as e:
                    logging.warning(f"[IdleCheck] Failed to parse {group_name}: {e}")

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logging.error(f"[GroupCleanup] Failed: {e}")

        return left


# === External Feed Hook ===
def add_group_mention(group_name: str, token_address: str):
    asyncio.create_task(group_mentions_queue.put((group_name, token_address)))
    log_event(f"📬 Group mention queued: {group_name} → {token_address}")

# === Exported Runner ===
async def run_telegram_signal_scanner():
    await telegram_group_listener.run()

# === Telegram Token Mention Counter ===
async def get_token_mention_count(minutes: int = 30) -> Dict[str, int]:
    """
    Scans recent group messages and counts token mentions.
    """
    messages = await get_recent_group_messages(minutes=minutes)
    mention_counts: Dict[str, int] = defaultdict(int)

    for msg in messages:
        tokens = extract_tokens_from_text(msg.get("text", ""))
        for token in tokens:
            mention_counts[token] += 1
    return dict(mention_counts)

# === Singleton ===
telegram_group_listener = TelegramGroupListener()
