def get_memory(key: str, default=None):
	"""
	Proxy to persistent memory recall.
	"""
	return mm_get_memory(key, default)

def set_memory(key: str, value):
	"""
	Proxy to persistent memory setter.
	"""
	mm_set_memory(key, value)
# --- MEMORY SECTION ---
import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict

class LibrarianMemory:
	def __init__(self, persistence_dir):
		self.persistence_dir = persistence_dir
		self._memory_store = {}
		self._memory_file = os.path.expanduser("/home/ubuntu/nyx/runtime/memory/librarian.json")

	def get_memory(self, key: str, default=None):
		return self._memory_store.get(key, default)

	def set_memory(self, key: str, value):
		self._memory_store[key] = value

	def del_memory(self, key: str):
		if key in self._memory_store:
			del self._memory_store[key]

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

	def save_memory(self):
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
			tmp = f"{self._memory_file}.tmp"
			with open(tmp, "w") as f:
				json.dump(self._memory_store, f, indent=2, default=str)
			os.replace(tmp, self._memory_file)
		except Exception as e:
			print(f"[LibrarianMemory] Failed to save memory: {e}")

	def load_all(self):
		if os.path.exists(self._memory_file):
			try:
				with open(self._memory_file, "r") as f:
					self._memory_store = json.load(f)
			except Exception as e:
				print(f"[LibrarianMemory] Failed to load memory: {e}")
