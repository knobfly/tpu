import json
import os
from datetime import datetime

from core.llm.llm_brain import embed_text
from memory.token_memory_index import token_memory_index
from utils.logger import log_event


async def load_chat_logs_at_startup(directory="/home/ubuntu/nyx/runtime/data/chat_chunks"):
    log_event(f"🧠 Loading chat logs from `{directory}`...")
    total_embedded = 0

    for filename in os.listdir(directory):
        if not filename.endswith(".json"):
            continue

        path = os.path.join(directory, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for entry in data:
                text = entry.get("text") or entry.get("message")
                if not text or len(text) < 12:
                    continue

                timestamp = entry.get("timestamp") or entry.get("date") or datetime.utcnow().isoformat()
                group = entry.get("group") or entry.get("group_name") or "unknown"
                author = entry.get("user") or entry.get("author") or "anon"

                embedded = await embed_text(text)
                token_memory_index.add({
                    "embedding": embedded,
                    "text": text,
                    "meta": {
                        "source": group,
                        "author": author,
                        "timestamp": timestamp,
                        "loader": filename
                    }
                })

                total_embedded += 1

        except Exception as e:
            log_event(f"❌ Failed to load `{filename}`: {e}")

    log_event(f"✅ Chat memory ingestion complete. Embedded {total_embedded} messages.")
