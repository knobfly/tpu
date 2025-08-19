# inputs/social/telegram_group_scanner.py
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Set, Tuple

from core.live_config import config
from core.llm.lexicon_tracker import lexicon_tracker
from inputs.social.telegram_clients import ensure_user_client_started  # singleton client
from librarian.data_librarian import librarian
from telethon import events
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.types import MessageEntityMention, MessageEntityTextUrl
from utils.crash_guardian import crash_guardian
from utils.logger import log_event
from utils.service_status import update_status
from cortex.core_router import handle_event
from utils.meta_keywords import add_keywords

# =========================
# Config / constants
# =========================
SCAN_INTERVAL = 30               # main loop heartbeat
DISCOVERY_SLEEP = 300            # how often to sweep dialogs for new links/mentions
DISCOVERY_MSG_LIMIT = 20         # messages to scan per dialog
DISCOVERY_KEYWORDS = {"sol", "token", "coin", "pump", "airdrop", "sniper"}

# optional: seed groups to join at startup (handles or invite URLs)
initial_groups: list[str] = config.get("tg_seed_groups", []) or []

# Tokens: $TICKER or bare ALLCAPS up to 6 chars
TOKEN_PATTERN = re.compile(r"\$[A-Za-z]{2,10}|\b[A-Z]{2,6}\b")

# =========================
# Runtime state
# =========================
_handler_bound = False  # guard against double attach


class TelegramGroupScanner:
    def __init__(self):
        # de-dupe and runtime memory
        self.joined_groups: Set[str] = set()            # normalized handles/hashes
        self.seen_links: Set[str] = set()               # t.me links
        self.processed_msgs: Set[Tuple[int, int]] = set()  # (chat_id, msg_id)
        self._running = False

    # ---------- helpers ----------
    @staticmethod
    def _norm_handle_or_hash(val: str) -> str:
        v = (val or "").strip()
        if v.startswith("@"):
            v = v[1:]
        if "t.me/" in v:
            v = v.split("t.me/", 1)[1]
        # joinchat or +hash invite
        if "/joinchat/" in v:
            return f"hash:{v.split('/joinchat/', 1)[1]}"
        if v.startswith("+"):
            return f"hash:{v[1:]}"
        if "/+" in v:
            return f"hash:{v.split('/+', 1)[1]}"
        # channel/group handle
        return f"user:{v.rsplit('/', 1)[-1].lower()}"

    async def _join_from_handle_or_url(self, client, handle_or_url: str):
        """
        Accepts:
          - 'groupname' or '@groupname'
          - 't.me/groupname'
          - 't.me/+abcdef' or 't.me/joinchat/abcdef' (invite hash)
        """
        val = (handle_or_url or "").strip()
        key = self._norm_handle_or_hash(val)
        if key in self.joined_groups:
            return

        try:
            # invite hash paths
            if key.startswith("hash:"):
                invite_hash = key.split("hash:", 1)[1]
                await client(ImportChatInviteRequest(invite_hash))
                self.joined_groups.add(key)
                log_event(f"[TG GroupScanner] 🌐 Auto-joined via invite hash: {invite_hash}")
                return

            # handle (username) path
            handle = key.split("user:", 1)[1]
            await client(JoinChannelRequest(handle))
            self.joined_groups.add(key)
            log_event(f"[TG GroupScanner] 🌐 Auto-joined via URL/handle: {handle}")

        except Exception as e:
            logging.warning(f"[TG GroupScanner] Failed to join {handle_or_url}: {e}")

    async def _join_initial_groups(self, client):
        for g in initial_groups:
            try:
                await self._join_from_handle_or_url(client, g)
            except Exception as e:
                logging.warning(f"[TG GroupScanner] ❌ Failed seed join {g}: {e}")

    # ---------- message handling ----------
    async def _scan_message(self, event):
        try:
            update_status("telegram_group_scanner")

            msg = event.message
            text = msg.message or ""
            if not text:
                return

            chat = await event.get_chat()
            group_name = (
                getattr(chat, "title", None)
                or getattr(chat, "username", None)
                or "UnknownGroup"
            )
            chat_id = getattr(chat, "id", None) or 0
            key = (int(chat_id), int(msg.id))

            if key in self.processed_msgs:
                return
            self.processed_msgs.add(key)

            matches = TOKEN_PATTERN.findall(text)
            if not matches:
                return

            for match in matches:
                symbol = match.replace("$", "")
                log_event(f"[TG GroupScanner] 🧠 Found ${symbol} in {group_name}")

                # learn symbol in lexicon (best effort)
                try:
                    lexicon_tracker().add(symbol, context="tg_group_scan", source=group_name)
                except Exception:
                    pass

                # persist a small record for later
                try:
                    librarian.record_signal({
                        "source": "telegram_group",
                        "symbol": symbol,
                        "group": group_name,
                        "message": text[:250],
                        "timestamp": datetime.utcnow().isoformat()
                    })

                    await handle_event({
                        "token": mint_address_or_best_guess,   # prefer mint if you have it
                        "action": "social_update",
                        "messages": [{"text": text, "group": group_name, "ts": datetime.utcnow().isoformat()}],
                        "source": "telegram_group",
                    })

                    scope = token_contract or token_symbol or f"tg:{chat_id}"
                    add_keywords(scope=scope, keywords=keywords, source="telegram", ref=str(message_id))
                except Exception:
                    pass

        except Exception as e:
            logging.warning(f"[TG GroupScanner] ❌ Error processing message: {e}")

    # ---------- discovery ----------
    async def _dynamic_discovery(self, client):
        """
        Periodically scans your dialogs for recent messages that contain
        discovery keywords and tries to join @mentions and t.me links.
        """
        while self._running:
            try:
                dialogs = await client.get_dialogs()
                for dialog in dialogs:
                    # groups/channels only
                    if not getattr(dialog, "is_group", False) and not getattr(dialog, "is_channel", False):
                        continue
                    if not getattr(dialog, "title", None):
                        continue

                    messages = await client.get_messages(dialog.id, limit=DISCOVERY_MSG_LIMIT)
                    for msg in messages:
                        t = (getattr(msg, "message", None) or "").lower()
                        if not t or not any(k in t for k in DISCOVERY_KEYWORDS):
                            continue

                        entities = msg.entities or []
                        for ent in entities:
                            # @mentions
                            if isinstance(ent, MessageEntityMention):
                                handle = msg.message[ent.offset + 1: ent.offset + ent.length]
                                if handle:
                                    await self._join_from_handle_or_url(client, handle)

                            # t.me links
                            if isinstance(ent, MessageEntityTextUrl) and ent.url and "t.me" in ent.url:
                                url = ent.url.strip()
                                if url not in self.seen_links:
                                    self.seen_links.add(url)
                                    await self._join_from_handle_or_url(client, url)

            except Exception as e:
                logging.warning(f"[TG GroupScanner] Discovery loop error: {e}")

            await asyncio.sleep(DISCOVERY_SLEEP)

    # ---------- runner ----------
    async def run(self):
        """
        Long-lived service:
          - ensures client
          - binds handler once
          - joins seed groups
          - spawns discovery loop
          - heartbeats forever so CrashGuardian doesn’t restart it
        """
        global _handler_bound
        client = await ensure_user_client_started()

        if not _handler_bound:
            @client.on(events.NewMessage)
            async def _handler(event):
                await self._scan_message(event)

            _handler_bound = True
            log_event("[TG GroupScanner] Handler attached to user client.")

        await self._join_initial_groups(client)

        # start discovery loop
        self._running = True
        asyncio.create_task(self._dynamic_discovery(client))

        log_event("📡 Telegram Group Scanner started.")
        # keep alive forever with a heartbeat
        while self._running:
            try:
                crash_guardian.beat("TelegramGroupScanner")
            except Exception:
                pass
            await asyncio.sleep(SCAN_INTERVAL)


# ===== Singleton export =====
telegram_group_scanner = TelegramGroupScanner()
