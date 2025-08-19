import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from typing import List, Optional

from core.live_config import config
from inputs.social.telegram_clients import ensure_user_client_started  # singleton user client
from librarian.data_librarian import librarian
from telethon import events
from telethon.errors import (
    FloodWaitError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    UserAlreadyParticipantError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from utils.logger import log_event

# ---- Config ----
USER_CFG = (config.get("telegram_user") or {})

JOIN_RATE_LIMIT = int(USER_CFG.get("join_rate_limit_sec", 90))

CAPTCHA_CFG = USER_CFG.get("captcha") or {}
CAPTCHA_ENABLED = bool(CAPTCHA_CFG.get("enabled", True))
BTN_KEYWORDS = [s.lower() for s in CAPTCHA_CFG.get("button_keywords", ["verify", "human", "start"])]

EXTERNAL = CAPTCHA_CFG.get("external_solver") or {}
EXT_SOLVER_ENABLED = bool(EXTERNAL.get("enabled", True))
EXT_PROVIDER = EXTERNAL.get("provider", "2captcha")
EXT_API_KEY = EXTERNAL.get("api_key")
EXT_TIMEOUT = int(EXTERNAL.get("timeout_sec", 90))

DISCOVERY_ENABLED = bool(USER_CFG.get("enable_discovery", True))
GROUP_DISCOVERY_KEYWORDS = USER_CFG.get("discovery_keywords") or [
    "pump", "call", "alpha", "gem", "sniper", "crypto", "coin", "sol", "meme",
    "token", "airdrop", "launch", "new", "mint", "presale", "community"
]
MAX_GROUPS = int(USER_CFG.get("max_autojoin_groups", 5))
SLEEP_BETWEEN = float(USER_CFG.get("sleep_between_sec", 10.0))
FLOOD_SAFETY_CAP = int(USER_CFG.get("flood_safety_cap_sec", 3600))

FAIL_PATH = os.path.expanduser("/home/ubuntu/nyx/runtime/data/tg_join_failures.json")
FAIL_TTL_DAYS = 14

ATTEMPT_PATH = os.path.expanduser("/home/ubuntu/nyx/runtime/data/tg_join_attempts.json")
ATTEMPT_TTL_SEC = int(USER_CFG.get("join_attempt_ttl_sec", 6 * 60 * 60))  # 6h default
_attempted_runtime: dict[str, float] = {}

# ---- Runtime ----
_last_join_at: Optional[datetime] = None
_client = None  # set in start_telegram_user_joiner()

# Invite patterns
JOINCHAT_RE = re.compile(r"(?:t\.me/|telegram\.me/)?(?:joinchat/|\+)([A-Za-z0-9_-]+)$", re.IGNORECASE)
CAPTCHA_BOTS = {"rose", "combot", "shieldy", "captcha", "banhammer", "bee", "pistol", "gatekeeper"}


# -------------------------
# Helpers: keys & persistence
# -------------------------
def _normalize_invite(invite: str) -> str:
    """Trim protocols, deep-links, and post indices (…/123)."""
    raw = (invite or "").strip()
    base = raw.split("?")[0]
    base = base.replace("https://", "").replace("http://", "")
    base = base.replace("t.me/", "").replace("telegram.me/", "")
    parts = base.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-1].isdigit():  # drop post index
        base = "/".join(parts[:-1])
    return base

def _key_for(invite: str) -> str:
    """
    Single canonical key for both join-chat hashes and usernames.
    Always lowercased to avoid dupes.
    """
    base = _normalize_invite(invite)
    m = JOINCHAT_RE.search(base)
    if m:
        return f"hash:{m.group(1).lower()}"
    handle = base.split("/")[-1].lstrip("@").lower()
    return f"user:{handle}"

def _now_iso() -> str:
    return datetime.utcnow().isoformat()

def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _save_json(path: str, d: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)

def _load_fail() -> dict:
    return _load_json(FAIL_PATH)

def _save_fail(d: dict) -> None:
    _save_json(FAIL_PATH, d)

def _load_attempts() -> dict:
    return _load_json(ATTEMPT_PATH)

def _save_attempts(d: dict) -> None:
    _save_json(ATTEMPT_PATH, d)

def _touch_attempt(invite: str) -> None:
    k = _key_for(invite)
    ts = time.time()
    _attempted_runtime[k] = ts
    try:
        disk = _load_attempts()
        disk[k] = ts
        _save_attempts(disk)
    except Exception:
        pass

def _recently_attempted(invite: str) -> bool:
    k = _key_for(invite)
    now = time.time()
    ts = _attempted_runtime.get(k)
    if ts and now - ts < ATTEMPT_TTL_SEC:
        return True
    try:
        disk = _load_attempts()
        ts = float(disk.get(k, 0))
        return (now - ts) < ATTEMPT_TTL_SEC if ts else False
    except Exception:
        return False

def record_join_result(invite: str, ok: bool, reason: str) -> None:
    d = _load_fail()
    k = _key_for(invite)
    rec = d.get(k) or {"count": 0}
    rec["last"] = _now_iso()
    rec["ok"] = bool(ok)
    rec["reason"] = str(reason)
    rec["count"] = int(rec.get("count", 0)) + 1
    d[k] = rec
    _save_fail(d)

def _skip_by_fail_ttl(invite: str) -> bool:
    d = _load_fail()
    k = _key_for(invite)
    rec = d.get(k)
    if not rec:
        return False
    try:
        last = datetime.fromisoformat(rec.get("last", ""))
    except Exception:
        return False
    age = datetime.utcnow() - last
    if age > timedelta(days=FAIL_TTL_DAYS):
        # expire old record
        d.pop(k, None)
        _save_fail(d)
        return False
    # skip if we recently succeeded or failed
    return True

def should_skip_join(invite: str) -> bool:
    # persistent failures/recents
    if _skip_by_fail_ttl(invite):
        return True
    # recent in-memory/disk attempt
    if _recently_attempted(invite):
        return True
    return False


# -------------------------
# Public API
# -------------------------
def get_user_client():
    return _client


async def start_telegram_user_joiner():
    """
    Boots the Telethon user client, attaches listeners, schedules loops,
    and stays alive (never returns) so supervisors don’t restart it.
    """
    global _client
    try:
        _client = await ensure_user_client_started()
        me = await _client.get_me()
        log_event(f"👤 Telegram user online: @{getattr(me,'username',None) or me.id}")
    except sqlite3.OperationalError as e:
        log_event(f"❌ [TG-UserJoiner] failed to start client: {e}")
        return
    except Exception as e:
        log_event(f"❌ [TG-UserJoiner] unexpected start error: {e}")
        return

    # --- Listeners (set exactly once) ---
    if CAPTCHA_ENABLED:
        @_client.on(events.NewMessage)
        async def _captcha_listener(event):
            try:
                await _maybe_handle_captcha(event)
            except Exception as e:
                logging.warning(f"[TG-UserJoiner] captcha handler err: {e}")

    @_client.on(events.NewMessage)
    async def _nyx_msg_monitor(event):
        try:
            if not event.message or not event.message.message:
                return
            chat = await event.get_chat()
            chat_title = (
                getattr(chat, 'title', None)
                or getattr(chat, 'username', None)
                or str(getattr(chat, 'id', ''))
            )
            text = event.message.message
            text_l = (text or "").lower()

            if any(k in text_l for k in GROUP_DISCOVERY_KEYWORDS):
                # best-effort, optional deps
                try:
                    from inputs.social.token_extraction import extract_tokens_from_text
                except Exception:
                    extract_tokens_from_text = lambda s: []  # type: ignore

                try:
                    from core.llm.sentiment_reason import extract_sentiment_reason
                except Exception:
                    extract_sentiment_reason = lambda _: "signal_detected"  # type: ignore

                try:
                    tokens = extract_tokens_from_text(text)
                except Exception:
                    tokens = []

                try:
                    reason = await extract_sentiment_reason(text_l) or "signal"
                except Exception:
                    reason = "signal"

                # tag + store
                for token in tokens:
                    try:
                        from strategy.strategy_memory import tag_token_result
                        tag_token_result(token, "telegram_group")
                    except Exception:
                        pass

                try:
                    await librarian.ingest_stream_event({
                        "type": "telegram_message",
                        "chat_id": getattr(chat, "id", None),
                        "chat_title": chat_title,
                        "message_id": event.id,
                        "text": text,
                        "tokens": tokens,
                        "reason": reason,
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                except Exception:
                    pass
        except Exception as e:
            logging.warning(f"[TG-UserJoiner] msg monitor err: {e}")

    # --- Loops (schedule once) ---
    if DISCOVERY_ENABLED:
        asyncio.create_task(_discover_and_join_groups_loop())
    asyncio.create_task(_heartbeat_loop())

    # --- Keep alive ---
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        return


async def join_by_invite(invite: str, reason: str = "auto") -> bool:
    """
    Join via:
      - t.me/joinchat/<hash> or t.me/+<hash>
      - t.me/<handle> or plain handle
    Records success/failure and caches attempts to avoid duplicates.
    """
    global _last_join_at
    if _client is None:
        log_event("❌ [TG-UserJoiner] client not started")
        return False

    # Skip known-bad or recently attempted targets
    if should_skip_join(invite):
        log_event(f"⛔ Skipping target: {invite}")
        return False

    # Mark attempt immediately to suppress rapid dupes
    _touch_attempt(invite)

    # rate limit between joins
    if _last_join_at and (datetime.utcnow() - _last_join_at).total_seconds() < JOIN_RATE_LIMIT:
        await asyncio.sleep(JOIN_RATE_LIMIT)

    try:
        raw = _normalize_invite(invite)
        m = JOINCHAT_RE.search(raw)
        if m:
            # Invite hash flow
            invite_hash = m.group(1)
            try:
                await _client(CheckChatInviteRequest(invite_hash))
            except InviteHashInvalidError:
                log_event(f"❌ invalid invite hash: {invite}")
                record_join_result(invite, False, "invalid_invite")
                return False
            chat = await _client(ImportChatInviteRequest(invite_hash))
            entity = chat.chats[0] if getattr(chat, "chats", None) else chat
        else:
            # Username/handle flow
            handle = raw.rstrip("/").split("/")[-1]
            # quick redundant-join check
            try:
                dialogs = await _client.get_dialogs()
                if any((getattr(d.entity, "username", "") or "").lower() == handle.lower() for d in dialogs):
                    log_event(f"ℹ️ Already in dialogs: {invite}")
                    record_join_result(invite, True, "already_in_dialogs")
                    return True
            except Exception:
                pass

            entity = await _client.get_entity(handle)
            await _client(JoinChannelRequest(entity))

        title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(entity.id)
        _last_join_at = datetime.utcnow()
        log_event(f"✅ Joined Telegram: {title}")

        try:
            await librarian.ingest_stream_event({
                "type": "telegram_join",
                "chat_id": getattr(entity, "id", None),
                "chat_title": title,
                "invite": invite,
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat()
            })
        except Exception:
            pass

        record_join_result(invite, True, "joined")
        return True

    except FloodWaitError as fw:
        wait_s = min(getattr(fw, "seconds", JOIN_RATE_LIMIT) + 3, FLOOD_SAFETY_CAP)
        log_event(f"⏳ Flood wait: {wait_s}s; backing off")
        await asyncio.sleep(wait_s)
        return False

    except UserAlreadyParticipantError:
        log_event(f"ℹ️ Already a participant: {invite}")
        record_join_result(invite, True, "already_participant")
        return True

    except (InviteHashExpiredError, InviteHashInvalidError):
        log_event(f"❌ Invite expired/invalid: {invite}")
        record_join_result(invite, False, "invalid_invite")
        return False

    except Exception as e:
        logging.warning(f"[TG-UserJoiner] join error for {invite}: {e}")
        record_join_result(invite, False, str(e))
        return False


# ---------- Captcha handling ----------
async def _maybe_handle_captcha(event):
    """
    Handles common Telegram captcha patterns:
    - Inline button 'verify' / 'I am human'
    - Math: '2+3' → '5'
    - Keyword: 'type 7' or 'send 🐸'
    """
    global _last_join_at

    # react only within 3 minutes after a join
    if _last_join_at and (datetime.utcnow() - _last_join_at) > timedelta(minutes=3):
        return

    sender = (await event.get_sender())
    sender_un = (sender.username or "").lower()
    txt = (event.raw_text or "").lower()

    looks_like_captcha = any(k in txt for k in ["captcha", "verify", "human", "bot check", "type"])
    if sender_un not in CAPTCHA_BOTS and not looks_like_captcha:
        return

    # 1) Inline buttons
    if event.buttons:
        for row in event.buttons:
            for btn in row:
                label = (btn.text or "").lower()
                if any(k in label for k in BTN_KEYWORDS):
                    await event.click(btn)
                    log_event(f"🤖 Captcha: pressed '{btn.text}'")
                    await asyncio.sleep(2)
                    return

    # 2) Math / keyword
    m = re.search(r"(\d+)\s*([+\-x×*÷/])\s*(\d+)", txt)
    if m:
        a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
        if op in {"x", "×", "*"}:
            ans = a * b
        elif op in {"÷", "/"}:
            ans = a // b if b else 0
        elif op == "+":
            ans = a + b
        else:
            ans = a - b
        await event.reply(str(ans))
        log_event(f"🤖 Captcha: solved math {a}{op}{b}={ans}")
        return

    k = re.search(r"type\s+([a-z0-9]+)|send\s+([^\s]+)", txt)
    if k:
        ans = k.group(1) or k.group(2)
        await event.reply(ans)
        log_event(f"🤖 Captcha: keyword '{ans}'")
        return

    if EXT_SOLVER_ENABLED and ("http://" in txt or "https://" in txt):
        log_event("🌐 Captcha link detected — external solver hook available (no-op here).")


# ---------- Discovery (no bulk import) ----------
async def _discover_and_join_groups_loop():
    """
    Periodically searches public groups by keywords and attempts to join.
    """
    if _client is None:
        return
    log_event("🔎 Telegram discovery loop started.")
    backoff = 5
    while True:
        try:
            # Pause discovery if we already have many dialogs
            try:
                dialogs = await _client.get_dialogs()
                if MAX_GROUPS and len(dialogs) >= MAX_GROUPS:
                    await asyncio.sleep(600)
                    continue
            except Exception:
                pass

            q = " ".join(GROUP_DISCOVERY_KEYWORDS)[:64]
            result = await _client(SearchRequest(q=q, limit=10))
            for chat in getattr(result, "chats", []):
                username = getattr(chat, "username", None)
                if not username:
                    continue
                invite = f"https://t.me/{username}"

                if should_skip_join(invite):
                    continue

                _ = await join_by_invite(invite, reason="discovery")
                await asyncio.sleep(SLEEP_BETWEEN)  # don't hammer

            # After a sweep, DM yourself invalid targets summary (optional)
            try:
                bad = [k for k, rec in _load_fail().items() if not rec.get("ok")]
                if bad:
                    pretty = [i.replace("user:", "").replace("hash:", "") for i in bad]
                    await send_invalid_targets_to_inbox(_client, pretty)
            except Exception:
                pass
            backoff = 5
        except Exception as e:
            logging.error(f"[Discover Loop Error] {e}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)
        await asyncio.sleep(300)


# ---------- Keepalive ----------
async def _heartbeat_loop():
    while True:
        try:
            if _client:
                await _client.get_dialogs(limit=1)
        except Exception:
            pass
        await asyncio.sleep(60)


async def send_invalid_targets_to_inbox(client, invites: List[str]) -> None:
    if not invites:
        return
    body = "⚠️ Invalid/failed Telegram targets (cached):\n" + "\n".join(f"• {i}" for i in invites[:50])
    try:
        await client.send_message("me", body)
    except Exception:
        pass
