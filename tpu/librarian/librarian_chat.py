# librarian_chat.py
# Chat ingestion logic split from data_librarian.py

import gzip
import json
import os
from utils.logger import log_event

class LibrarianChat:
    def __init__(self, runtime):
        self.runtime = runtime

    async def ingest_chat_messages(self, messages: list[dict]) -> None:
        os.makedirs("/home/ubuntu/nyx/runtime/library/chats", exist_ok=True)
        path = "/home/ubuntu/nyx/runtime/library/chats/chat_messages.jsonl.gz"

        seen_keys = set()
        deduped = []
        for m in messages:
            key = (m.get("chat_id"), m.get("message_id"))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(m)

        if not deduped:
            return

        with gzip.open(path, "ab") as f:
            for m in deduped:
                f.write((json.dumps(m, ensure_ascii=False) + "\n").encode("utf-8"))

        bucket = self.runtime.get("chat_messages")
        if bucket is None:
            bucket = self.runtime["chat_messages"] = []
        bucket.extend(deduped)

        log_event(f"[Librarian] Ingested {len(deduped)} new chat messages → {path}")

    async def ingest_records(self, kind: str, records: list[dict]) -> None:
        if kind == "chat_message":
            await self.ingest_chat_messages(records)
            return
        raise RuntimeError(f"Unsupported ingest kind: {kind}")
