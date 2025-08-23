
import asyncio
import logging
import os
import random
import time
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import aiohttp
from core.live_config import config
from utils.logger import log_event
from utils.service_status import update_status
from utils.token_utils import fetch_token_metadata

BASE_URL_PRO = "https://pro-api.solscan.io/"
BASE_URL_PUBLIC = "https://public-api.solscan.io/"
DEFAULT_TIMEOUT = 10

class SolscanProClient:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or config.get("solscan_api_key")
        self.headers_pro = {
            "Accept": "application/json",
            "token": self.api_key
        }
        self.headers_public = {
            "Accept": "application/json"
        }
        self.wallet_profiles: Dict[str, Dict[str, Any]] = {}
        self.wallet_categories: Dict[str, List[str]] = {
            "whale": [],
            "active": [],
            "newcomer": [],
            "influencer": [],
            "suspicious": [],
            "blacklisted": [],
            "notified": [],
        }
        self.notification_channels: List[str] = []
        self.api_usage: Dict[str, int] = {}
        self.alert_rules: List[Dict[str, Any]] = []
        self.supervisor = None
        self.streaming_enabled = False

    def _categorize_wallet(self, address: str, profile: Dict[str, Any]):
        tx_count = profile.get("tx_count", 0)
        tokens = profile.get("tokens", {}).get("data", []) if isinstance(profile.get("tokens"), dict) else []
        transfers = profile.get("transfers", {}).get("data", []) if isinstance(profile.get("transfers"), dict) else []
        balance_changes = profile.get("balance_changes", {}).get("data", []) if isinstance(profile.get("balance_changes"), dict) else []
        suspicious_score = sum(abs(b.get("change", 0)) for b in balance_changes)
        influencer_score = sum(1 for t in tokens if t.get("symbol", "").startswith("INFL"))

        if profile.get("is_whale") or any(t.get("amount", 0) > 100000 for t in tokens):
            self.wallet_categories["whale"].append(address)
        if tx_count > 100 or len(transfers) > 200:
            self.wallet_categories["active"].append(address)
        if profile.get("is_influencer") or influencer_score > 0:
            self.wallet_categories["influencer"].append(address)
        if profile.get("is_suspicious") or suspicious_score > 50000:
            self.wallet_categories["suspicious"].append(address)
        if profile.get("is_blacklisted"):
            self.wallet_categories["blacklisted"].append(address)
        if tx_count < 5 and len(tokens) < 3:
            self.wallet_categories["newcomer"].append(address)

        profile["historical_activity"] = {
            "tx_count": tx_count,
            "transfer_count": len(transfers),
            "token_count": len(tokens),
            "balance_change_total": suspicious_score,
            "influencer_score": influencer_score,
        }

    async def get_account_tokens(self, address: str, public: bool = False) -> dict:
        endpoint = f"account/tokens?address={address}"
        return await self._fetch(endpoint, use_public=public)

    async def get_account_transfers(self, address: str, public: bool = False) -> dict:
        endpoint = f"account/transfers?address={address}"
        return await self._fetch(endpoint, use_public=public)

    async def get_account_balance_changes(self, address: str, public: bool = False) -> dict:
        endpoint = f"account/balancechanges?address={address}"
        return await self._fetch(endpoint, use_public=public)

    async def get_account_transactions(self, address: str, public: bool = False) -> dict:
        endpoint = f"account/transactions?address={address}"
        return await self._fetch(endpoint, use_public=public)

    def setup_streaming(self):
        if not self.streaming_enabled:
            return
        logging.info("[SolscanPro] Streaming setup initialized (currently disabled)")

    def handle_stream_event(self, event: Dict[str, Any]):
        address = event.get("address")
        event_type = event.get("type")
        details = event.get("details", {})
        asyncio.create_task(self.notify_wallet_event(address, event_type, details))
        self.handle_auto_response(address, event_type, details)

    def set_alert_rules(self, rules: List[Dict[str, Any]]):
        self.alert_rules = rules

    def check_alert_rules(self, profile: Dict[str, Any]):
        for rule in self.alert_rules:
            field = rule.get("field")
            op = rule.get("op")
            value = rule.get("value")
            msg = rule.get("message", "Custom alert triggered")
            v = profile.get(field)
            if op == ">" and v is not None and v > value:
                asyncio.create_task(self.notify_wallet_event(profile["address"], msg, profile))
            elif op == "<" and v is not None and v < value:
                asyncio.create_task(self.notify_wallet_event(profile["address"], msg, profile))
            elif op == "==" and v == value:
                asyncio.create_task(self.notify_wallet_event(profile["address"], msg, profile))
            elif op == "regex" and v is not None and re.match(value, str(v)):
                asyncio.create_task(self.notify_wallet_event(profile["address"], msg, profile))

    async def enrich_external_profile(self, address: str) -> Dict[str, Any]:
        external = {
            "twitter": f"@user{random.randint(1000,9999)}",
            "discord": f"user{random.randint(1000,9999)}",
            "nft_count": random.randint(0, 20),
            "reputation": random.uniform(0, 10),
        }
        return external

    async def enrich_wallet_profile(self, address: str, public=False) -> Dict[str, Any]:
        tokens = await self.get_account_tokens(address, public=public)
        transfers = await self.get_account_transfers(address, public=public)
        balance_changes = await self.get_account_balance_changes(address, public=public)
        transactions = await self.get_account_transactions(address, public=public)
        external = await self.enrich_external_profile(address)
        profile = {
            "address": address,
            "tokens": tokens,
            "transfers": transfers,
            "balance_changes": balance_changes,
            "transactions": transactions,
            "tx_count": len(transactions.get("data", [])) if isinstance(transactions, dict) else 0,
            "is_whale": any(t.get("amount", 0) > 100000 for t in tokens.get("data", []) if isinstance(tokens, dict)),
            "is_influencer": any(t.get("symbol", "").startswith("INFL") for t in tokens.get("data", []) if isinstance(tokens, dict)),
            "is_suspicious": any(abs(b.get("change", 0)) > 50000 for b in balance_changes.get("data", []) if isinstance(balance_changes, dict)),
            "is_blacklisted": address in self.wallet_categories.get("blacklisted", []),
            "external": external,
        }
        self.wallet_profiles[address] = profile
        self._categorize_wallet(address, profile)
        self.check_alert_rules(profile)
        return profile

    def detect_behavioral_patterns(self, profile: Dict[str, Any]):
        tx_count = profile.get("tx_count", 0)
        transfer_count = profile.get("historical_activity", {}).get("transfer_count", 0)
        influencer_score = profile.get("historical_activity", {}).get("influencer_score", 0)
        if tx_count > 500 and transfer_count > 1000:
            asyncio.create_task(self.notify_wallet_event(profile["address"], "bot_like_activity", profile))
        if influencer_score > 10:
            asyncio.create_task(self.notify_wallet_event(profile["address"], "coordinated_influencer_activity", profile))

    def handle_auto_response(self, address: str, event_type: str, details: Dict[str, Any]):
        if event_type == "suspicious_activity":
            self.wallet_categories["blacklisted"].append(address)
            logging.info(f"[AutoResponse] Blacklisted {address} for suspicious activity.")
        elif event_type == "whale_activity":
            logging.info(f"[AutoResponse] Risk adjustment for whale {address}.")
        elif event_type == "bot_like_activity":
            logging.info(f"[AutoResponse] Bot-like activity detected for {address}.")

    def track_api_usage(self, endpoint: str):
        self.api_usage[endpoint] = self.api_usage.get(endpoint, 0) + 1
        logging.debug(f"[APIUsage] {endpoint}: {self.api_usage[endpoint]} calls")

    async def _fetch(self, endpoint: str, params: Optional[dict] = None, use_public: bool = False) -> dict:
        self.track_api_usage(endpoint)
        base_url = BASE_URL_PUBLIC if use_public else BASE_URL_PRO
        headers = self.headers_public if use_public else self.headers_pro
        url = f"{base_url}{endpoint}"
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, params=params, timeout=DEFAULT_TIMEOUT) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    logging.warning(f"[SolscanPro] {resp.status} for {endpoint}")
        except Exception as e:
            logging.error(f"[SolscanPro] Fetch failed: {e}")
        return {}

    def set_notification_channels(self, channels: List[str]):
        self.notification_channels = channels

    def route_notification(self, address: str, event: str, details: Dict[str, Any]):
        for channel in self.notification_channels:
            logging.info(f"[Notify:{channel}] {address} {event} {details}")

    async def notify_wallet_event(self, address: str, event: str, details: Dict[str, Any] = None):
        logging.info(f"[WalletNotify] {address}: {event} | {details}")
        self.wallet_categories["notified"].append(address)
        self.route_notification(address, event, details)
        if self.supervisor and hasattr(self.supervisor, "route_analytics_update"):
            await asyncio.sleep(0)
            self.supervisor.route_analytics_update({
                "wallet_event": event,
                "address": address,
                "details": details,
            })

    def initialize_wallet_tracking(self):
        self.wallet_profiles = {}
        self.wallet_categories = {
            "whale": [],
            "active": [],
            "newcomer": [],
            "influencer": [],
            "suspicious": [],
            "blacklisted": [],
            "notified": [],
        }
