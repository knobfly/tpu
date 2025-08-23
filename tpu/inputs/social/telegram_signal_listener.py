# inputs/social/telegram_signal_listener.py

import logging
import re
import time
from datetime import datetime
from typing import Dict, List

from core.live_config import config
from cortex.event_queue import enqueue_event_for_scoring
from inputs.onchain.firehose.firehose_replay_buffer import store_event
from inputs.social.telegram_alpha_router import maybe_post_alpha_ping
from inputs.social.telegram_clients import ensure_user_client_started  # ← singleton user client
from inputs.social.telegram_nlp_listener import analyze_telegram_message
from strategy.strategy_memory import tag_token_result
from telethon import events
from utils.logger import log_event
from utils.universal_input_validator import safe_parse

# Regex: rough Base58 (32–44 chars) for Solana addresses
SOL_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
# Ethereum address: 0x followed by 40 hex chars
ETH_ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
# BSC address: same format as Ethereum
BSC_ADDRESS_RE = ETH_ADDRESS_RE
# Simple $TICKER detection
TICKER_RE = re.compile(r"\$[A-Za-z0-9]{2,12}")

MIN_SIGNAL_CONFIDENCE = 0.35    # tighten as you wish
SIGNAL_THROTTLE_SECONDS = 10.0  # per chat anti-spam

_last_signal_ts_per_chat: Dict[int, float] = {}
_handler_bound = False  # avoid double-binding


def _too_soon(chat_id: int) -> bool:
    now = time.time()
    last = _last_signal_ts_per_chat.get(chat_id, 0.0)
    if now - last < SIGNAL_THROTTLE_SECONDS:
        return True
    _last_signal_ts_per_chat[chat_id] = now
    return False


def extract_signal_candidates(text: str) -> dict:
    sol_addresses = SOL_ADDRESS_RE.findall(text) or []
    eth_addresses = ETH_ADDRESS_RE.findall(text) or []
    bsc_addresses = BSC_ADDRESS_RE.findall(text) or []
    # Deduplicate all addresses
    all_addresses = set(sol_addresses + eth_addresses + bsc_addresses)
    tickers = TICKER_RE.findall(text) or []
    tickers = [t[1:] for t in tickers]  # strip $
    return {"addresses": list(all_addresses), "tickers": list(set(tickers))}


async def _handle_telegram_signal(text: str, chat_id: int, user_handle: str):
    # --- Spam/Flood Detection ---
    # Track message frequency per user/group
    from collections import defaultdict
    spam_tracker = getattr(_handle_telegram_signal, 'spam_tracker', None)
    if spam_tracker is None:
        spam_tracker = defaultdict(lambda: {'count': 0, 'last': 0})
        setattr(_handle_telegram_signal, 'spam_tracker', spam_tracker)
    now = time.time()
    spam_key = f"{chat_id}:{user_handle}"
    rec = spam_tracker[spam_key]
    if now - rec['last'] < 5:
        rec['count'] += 1
    else:
        rec['count'] = 1
    rec['last'] = now
    # If user floods >5 msgs in 30s, ignore
    if rec['count'] > 5:
        logging.info(f"[SpamDetect] Ignoring flood from {user_handle} in {chat_id}")
        return

    # --- Influencer/Admin Profiling ---
    # Track frequent posters and admins
    influencer_tracker = getattr(_handle_telegram_signal, 'influencer_tracker', None)
    if influencer_tracker is None:
        influencer_tracker = defaultdict(lambda: {'count': 0, 'last': 0})
        setattr(_handle_telegram_signal, 'influencer_tracker', influencer_tracker)
    influencer_rec = influencer_tracker[user_handle]
    influencer_rec['count'] += 1
    influencer_rec['last'] = now
    # If user posts >20 times/day, mark as influencer
    if influencer_rec['count'] > 20:
        try:
            from librarian.data_librarian import librarian
            librarian.catalog_influencer({
                'user': user_handle,
                'group': chat_id,
                'count': influencer_rec['count'],
                'last': datetime.utcnow().isoformat()
            })
        except Exception:
            pass

    # --- Scam/Rug Signal Detection ---
    scam_keywords = ["rug", "scam", "exit", "pull", "hack", "exploit", "stolen", "drain"]
    if any(k in text.lower() for k in scam_keywords):
        try:
            from librarian.data_librarian import librarian
            librarian.blacklist_source({
                'user': user_handle,
                'group': chat_id,
                'text': text,
                'timestamp': datetime.utcnow().isoformat()
            })
            logging.info(f"[ScamDetect] Blacklisted source {user_handle} in {chat_id}")
        except Exception:
            pass
        return
    """Core signal pipeline (telethon-agnostic)."""
    if not config.get("enable_telegram_learning", False):
        return

    if not text:
        return

    if _too_soon(chat_id):
        return

    # NLP pass (sentiment, keywords, embeds)
    try:
        nlp = await analyze_telegram_message(text, user_handle, chat_id)
    except Exception as e:
        logging.warning(f"[TGSignal] NLP failed: {e}")
        nlp = {}

    # Extract token/address candidates
    cands = extract_signal_candidates(text)
    if not cands["addresses"] and not cands["tickers"]:
        if config.get("debug_mode"):
            logging.debug("[TGSignal] No addresses/tickers found")
        return

    # Confidence (fix: previously undefined)
    # Prefer model-provided; else heuristic
    confidence = float(nlp.get("confidence", 0.0) or 0.0)
    if confidence <= 0:
        sent = float(nlp.get("sentiment", 0.0) or 0.0)  # assume -1..1
        has_t = 1.0 if cands["tickers"] else 0.0
        has_a = 1.0 if cands["addresses"] else 0.0
        # crude heuristic, clamp 0..1
        confidence = max(0.0, min(1.0, 0.2 + 0.35 * has_t + 0.25 * has_a + 0.2 * max(0.0, sent)))

    if confidence < MIN_SIGNAL_CONFIDENCE:
        if config.get("debug_mode"):
            logging.debug(f"[TGSignal] Low confidence {confidence:.2f}")
        return

    # Build event for cortex/scoring
    event = {
        "origin": "telegram",
        "chat_id": chat_id,
        "user": user_handle,
        "text": text,
        "tickers": cands["tickers"],
        "addresses": cands["addresses"],
        "sentiment": nlp.get("sentiment"),
        "toxicity": nlp.get("toxicity"),
        "keywords": nlp.get("keywords", []),
        "confidence": confidence,
        "ts": time.time(),
    }

    try:
        store_event(event)  # keep in short replay buffer
    except Exception:
        pass

    try:
        await enqueue_event_for_scoring(event)  # feed to cortex/score if desired
    except Exception as e:
        logging.warning(f"[TGSignal] enqueue_event_for_scoring failed: {e}")

    # Tag strategy memory for awareness
    for t in cands["tickers"]:
        try:
            tag_token_result(t, "telegram_ping", confidence * 100)
        except Exception:
            pass

    # Optional alpha ping back to groups (uses dedupe/cooldowns)
    try:
        await maybe_post_alpha_ping(event)
    except Exception as e:
        logging.debug(f"[TGSignal] alpha ping skipped: {e}")

    if config.get("debug_mode"):
        logging.debug(f"[TGSignal] Routed event: {event}")

    # --- Structured ingest to librarian ---
    from librarian.data_librarian import librarian
    msg_obj = {
        'group': None,
        'user': user_handle,
        'text': text,
        'keywords': nlp.get('keywords', []),
        'sentiment': nlp.get('sentiment'),
        'wallets': event.get('addresses', []),
        'tokens': event.get('tickers', []),
        'timestamp': datetime.utcnow().isoformat()
    }
    try:
        librarian.ingest_telegram_message(msg_obj)
    except Exception as e:
        logging.warning(f"[TGSignalListener] librarian ingest failed: {e}")

async def run_telegram_signal_listener():
    """
    Bind Telethon user-client message handler and process signals.
    """
    global _handler_bound
    client = await ensure_user_client_started()

    if not _handler_bound:
        @client.on(events.NewMessage)
        async def _listen_all_msgs(event):
            try:
                # Skip commands
                text = (event.raw_text or "").strip()
                if text.startswith("/"):
                    return

                chat = await event.get_chat()
                chat_id = getattr(chat, "id", None)
                if chat_id is None:
                    return

                sender = await event.get_sender()
                user_handle = getattr(sender, "username", None) or str(getattr(sender, "id", "")) or "unknown"

                await _handle_telegram_signal(text, int(chat_id), user_handle)
            except Exception as e:
                logging.warning(f"[TGSignal] Failed to process message: {e}")

        _handler_bound = True
        log_event("[TGSignal] Listener attached to user client.")
