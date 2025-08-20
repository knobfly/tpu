# librarian_maintenance.py
# Maintenance routines split from data_librarian.py

from datetime import datetime
from pathlib import Path
import asyncio
import logging
from utils.logger import log_event

class LibrarianMaintenance:
    def __init__(self, librarian):
        self.librarian = librarian

    async def library_maintenance_loop(self):
        base_dir = Path("/home/ubuntu/nyx/runtime/library")
        chats_dir = base_dir / "chats"
        base_dir.mkdir(parents=True, exist_ok=True)
        chats_dir.mkdir(parents=True, exist_ok=True)

        MAX_FILE_MB = 256
        KEEP_ROTATIONS = 5
        COMPACT_EVERY_SEC = 3 * 3600
        LIGHT_MAINT_EVERY_SEC = 600
        RETAIN_DAYS = 90

        if not hasattr(self.librarian, "_maint_lock"):
            self.librarian._maint_lock = asyncio.Lock()
        if not hasattr(self.librarian, "_last_compact_ts"):
            self.librarian._last_compact_ts = 0.0
        if not hasattr(self.librarian, "_last_light_ts"):
            self.librarian._last_light_ts = 0.0

        try:
            n = self.librarian.prune_memory()
            if n:
                log_event(f"[Maintenance] Pruned {n} memory entries")
        except Exception as e:
            logging.warning(f"[Maintenance] Error during upkeep: {e}")

        # Additional rotation/compaction logic can be added here
