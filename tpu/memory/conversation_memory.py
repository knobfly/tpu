# /conversation_memory.py
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

MEM_FILE = "/home/ubuntu/nyx/runtime/memory/conversation_memory.json"
PREF_FILE = "/home/ubuntu/nyx/runtime/memory/user_preferences.json"
os.makedirs(os.path.dirname(MEM_FILE), exist_ok=True)

DEFAULT_MAX_MESSAGES = 3000

def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"[ConvMemory] Failed to load {path}: {e}")
        return default

def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

class ConversationMemory:
    def __init__(self, max_messages: int = DEFAULT_MAX_MESSAGES, ttl_days: int = 30):
        self.max_messages = max_messages
        self.ttl_days = ttl_days
        self.data: Dict = _load_json(MEM_FILE, {"messages": []})
        self.prefs: Dict = _load_json(PREF_FILE, {"traits": {}, "rules": {}, "notes": []})

    def add(self, role: str, content: str, source: str = "telegram", meta: Optional[Dict] = None):
        entry = {
            "ts": datetime.utcnow().isoformat(),
            "role": role,
            "content": content,
            "source": source,
            "meta": meta or {}
        }
        self.data["messages"].append(entry)
        # keep bounded
        if len(self.data["messages"]) > self.max_messages:
            self.data["messages"] = self.data["messages"][-self.max_messages:]
        self._trim_ttl()
        self._persist()

    def get_recent(self, limit: int = 50) -> List[Dict]:
        return self.data.get("messages", [])[-limit:]

    def _trim_ttl(self):
        cutoff = datetime.utcnow() - timedelta(days=self.ttl_days)
        filtered = []
        for m in self.data.get("messages", []):
            try:
                ts = datetime.fromisoformat(m["ts"])
                if ts >= cutoff:
                    filtered.append(m)
            except:
                continue
        self.data["messages"] = filtered

    def _persist(self):
        _save_json(MEM_FILE, self.data)
        _save_json(PREF_FILE, self.prefs)

    # === Preference learning ===
    def learn_preference(self, key: str, value):
        self.prefs.setdefault("traits", {})[key] = value
        self._persist()

    def note(self, text: str):
        self.prefs.setdefault("notes", []).append({
            "ts": datetime.utcnow().isoformat(),
            "note": text
        })
        self._persist()

    def get_prefs(self) -> Dict:
        return self.prefs

    def summarize_for_prompt(self, limit:int=50) -> str:
        msgs = self.get_recent(limit)
        lines = []
        for m in msgs:
            who = "YOU" if m["role"] == "user" else "NYX"
            lines.append(f"{who}: {m['content']}")
        return "\n".join(lines[-limit:])
