# librarian_token.py
# Token profile management, enrichment, scoring, and token-related queries for DataLibrarian.

import time
import logging
from typing import Dict, Any, Optional, List, Set

class LibrarianToken:
	def __init__(self):
		self.seen_tokens: Dict[str, Dict[str, Any]] = {}
		self.token_profiles: Dict[str, Dict[str, Any]] = {}

	def ingest_token_profile(self, profile: Dict[str, Any]):
		contract = profile.get("contract")
		if not contract:
			logging.warning("[LibrarianToken] Ignored profile with no contract")
			return
		existing = self.seen_tokens.get(contract, {})
		merged = {**existing, **profile}
		merged["last_updated"] = time.time()
		merged["source"] = profile.get("source", merged.get("source", "unknown"))
		existing_tags = set(existing.get("tags", []))
		new_tags = set(profile.get("tags", []))
		merged["tags"] = list(existing_tags.union(new_tags))
		self.seen_tokens[contract] = merged
		self.token_profiles[contract] = merged
		logging.info(f"[LibrarianToken] Ingested token profile for {contract}")

	def has_seen_token(self, contract: str) -> bool:
		return contract in self.seen_tokens

	def get_token_summary(self, token: str) -> Dict[str, Any]:
		token_data = self.token_profiles.get(token, {})
		return {
			"token": token,
			"score": token_data.get("score", 0),
			"tags": list(token_data.get("tags", [])),
			"flags": list(token_data.get("flags", [])),
			"meta_theme": token_data.get("meta_theme", None),
			"created": token_data.get("created", None)
		}

