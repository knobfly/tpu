# exec/open_position_tracker.py
import asyncio
import json
import os
from datetime import datetime
from typing import Dict, List, Set

from core.live_config import config

# Dynamic stream control helpers (from the listener you installed)
from inputs.onchain.solana_stream_listener import (
    request_account_watch,
    request_logs_mention_watch,
    stop_account_watch,
    stop_logs_mention_watch,
)
from utils.logger import log_event
from utils.token_utils import get_wallet_tokens

POSITION_FILE = "/home/ubuntu/nyx/runtime/memory/open_positions.json"
STREAM_SUBS_FILE = "/home/ubuntu/nyx/runtime/memory/stream_subscriptions.json"


def _ensure_dirs():
    base = os.path.dirname(POSITION_FILE)
    if base and not os.path.exists(base):
        os.makedirs(base, exist_ok=True)


_ensure_dirs()


class OpenPositionTracker:
    """
    Tracks open positions and keeps the Solana stream in sync:
      - On add: watch token mint (logs mentions) + LP pool accounts (accountSubscribe)
      - On close: remove watches
      - On boot: replay watches for all 'holding' positions
    """
    def __init__(self):
        self.positions: Dict[str, Dict[str, dict]] = {}
        self.stream_subscriptions = {
            "commitment": config.get("stream_subscriptions", {}).get("commitment", "processed"),
            "programs": [],   # not used here, you can add Raydium/Orca program IDs if you want program-level watches
            "mentions": [],   # token mints (or wallets) to watch via logsSubscribe
            "accounts": [],   # LP pool/vault accounts to watch via accountSubscribe
            "signatures": [],
            "slot": True,
            "root": False
        }
        self.load_positions()
        self.load_stream_subs()

        # Try to bootstrap watches shortly after import if an event loop is running.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.bootstrap_stream_watches())
        except RuntimeError:
            # no running loop yet (startup path); caller can invoke bootstrap_stream_watches() later.
            pass

    # ---------- Persistence ----------

    def load_positions(self):
        if os.path.exists(POSITION_FILE):
            try:
                with open(POSITION_FILE, "r") as f:
                    self.positions = json.load(f)
            except Exception:
                self.positions = {}
                log_event("⚠️ Failed to load open positions, starting fresh.")
        else:
            self.positions = {}

    def save_positions(self):
        try:
            with open(POSITION_FILE, "w") as f:
                json.dump(self.positions, f, indent=2)
        except Exception as e:
            log_event(f"❌ Failed to save open positions: {e}")

    def load_stream_subs(self):
        if os.path.exists(STREAM_SUBS_FILE):
            try:
                with open(STREAM_SUBS_FILE, "r") as f:
                    self.stream_subscriptions = json.load(f)
            except Exception:
                log_event("⚠️ Failed to load stream subscriptions, starting fresh.")

    def save_stream_subs(self):
        try:
            with open(STREAM_SUBS_FILE, "w") as f:
                json.dump(self.stream_subscriptions, f, indent=2)
        except Exception as e:
            log_event(f"❌ Failed to save stream subscriptions: {e}")

    # ---------- LP Pool Resolution ----------

    async def resolve_lp_accounts(self, token_mint: str) -> Set[str]:
        """
        Resolve LP accounts (pool state, vaults, LP mint, etc.) for a token on Raydium/Orca.
        Tries multiple available utils. Returns a set of base58 account keys.
        """
        accounts: Set[str] = set()

        # Try shared router (if present)
        try:
            from utils.raydium_orca_router import get_lp_accounts as router_get_lp
            res = await _maybe_await(router_get_lp(token_mint))
            if isinstance(res, dict):
                for v in res.values():
                    if isinstance(v, str) and len(v) > 20:
                        accounts.add(v)
                # some routers return lists
                for k in ("vaults", "accounts", "extras"):
                    maybe = res.get(k)
                    if isinstance(maybe, list):
                        for a in maybe:
                            if isinstance(a, str) and len(a) > 20:
                                accounts.add(a)
            elif isinstance(res, (list, set, tuple)):
                for a in res:
                    if isinstance(a, str) and len(a) > 20:
                        accounts.add(a)
        except Exception as e:
            log_event(f"[OpenPositions] Router LP resolve fallback: {e}")

        # Try Raydium SDK (if present)
        try:
            from utils.raydium_sdk import get_pools_for_mint as ray_get_pools
            pools = await _maybe_await(ray_get_pools(token_mint))
            for p in _iter_pool_records(pools):
                for key in ("id", "ammId", "lpMint", "baseVault", "quoteVault", "openOrders", "targetOrders", "marketId"):
                    v = p.get(key)
                    if isinstance(v, str) and len(v) > 20:
                        accounts.add(v)
        except Exception as e:
            log_event(f"[OpenPositions] Raydium LP resolve fallback: {e}")

        # Try Orca SDK (if present)
        try:
            from utils.orca_sdk import get_pools_for_mint as orca_get_pools
            pools = await _maybe_await(orca_get_pools(token_mint))
            for p in _iter_pool_records(pools):
                for key in ("address", "poolTokenMint", "tokenVaultA", "tokenVaultB"):
                    v = p.get(key)
                    if isinstance(v, str) and len(v) > 20:
                        accounts.add(v)
        except Exception as e:
            log_event(f"[OpenPositions] Orca LP resolve fallback: {e}")

        if not accounts:
            log_event(f"[OpenPositions] ⚠️ No LP accounts resolved for {token_mint} (will still watch mint logs).")

        return accounts

    # ---------- Public API ----------

    def add_position(self, wallet, token, amount, price, strategy_id, token_symbol=None):
        if wallet not in self.positions:
            self.positions[wallet] = {}

        self.positions[wallet][token] = {
            "amount": amount,
            "buy_price": price,
            "buy_time": datetime.utcnow().isoformat(),
            "strategy": strategy_id,
            "token_symbol": token_symbol or token,
            "status": "holding",
            # persist resolved accounts so reboots reattach instantly
            "lp_accounts": self.positions[wallet].get(token, {}).get("lp_accounts", []),
        }
        self.save_positions()
        log_event(f"📌 New position tracked: {token} | {amount} @ {price} ({wallet})")

        # Kick off async resolution + subscribe
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._after_add_position_async(token))
        except RuntimeError:
            # No loop yet; user can call bootstrap later
            pass

    async def _after_add_position_async(self, token: str):
        # 1) ensure logs mention watch for token mint
        await self._ensure_mention_watch(token)

        # 2) resolve LP accounts and subscribe
        lp_accounts = await self.resolve_lp_accounts(token)
        if lp_accounts:
            await self._ensure_account_watches(lp_accounts)

        # 3) persist
        self._merge_lp_accounts_persisted(token, lp_accounts)

    def close_position(self, wallet, token):
        if wallet in self.positions and token in self.positions[wallet]:
            self.positions[wallet][token]["status"] = "closed"
            lp_accounts = set(self.positions[wallet][token].get("lp_accounts", []))
            self.save_positions()

            # Remove watches
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._remove_watches_for_token(token, lp_accounts))
            except RuntimeError:
                # If no loop yet, we still update persistence
                pass

            log_event(f"💼 Position closed: {token} ({wallet})")

    def get_open_positions(self):
        return self.positions

    async def check_positions(self):
        """
        Verifies positions using wallet token balances via direct RPC.
        Closes tracking if token not present anymore.
        """
        for wallet, tokens in self.positions.items():
            try:
                token_accounts = await get_wallet_tokens(wallet)
                live_balances = {
                    acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {}).get("mint"):
                    float(acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {}).get("tokenAmount", {}).get("uiAmount", 0))
                    for acc in token_accounts
                }

                for token, info in tokens.items():
                    if info.get("status") == "holding":
                        if token not in live_balances or live_balances[token] <= 0:
                            log_event(f"⚠️ Position missing from wallet: {token} in {wallet}")
                            self.close_position(wallet, token)

            except Exception as e:
                log_event(f"❌ Error checking positions for {wallet}: {str(e)}")

    async def bootstrap_stream_watches(self):
        """
        At boot/reconnect, re-attach watches for all holding positions.
        """
        # Re-apply persisted stream_subscriptions first (if you want)
        await self._replay_persisted_stream_subs()

        # Ensure watches for any open positions
        for wallet, tokens in self.positions.items():
            for token, info in tokens.items():
                if info.get("status") != "holding":
                    continue
                # mint mention
                await self._ensure_mention_watch(token)
                # lp accounts
                lp_accounts = set(info.get("lp_accounts") or [])
                if not lp_accounts:
                    lp_accounts = await self.resolve_lp_accounts(token)
                    self._merge_lp_accounts_persisted(token, lp_accounts)
                await self._ensure_account_watches(lp_accounts)

    # ---------- Internals ----------

    async def _ensure_mention_watch(self, token_mint: str):
        if token_mint not in self.stream_subscriptions["mentions"]:
            self.stream_subscriptions["mentions"].append(token_mint)
            self.save_stream_subs()
        try:
            await request_logs_mention_watch(token_mint)
            log_event(f"🔍 Stream: mention watch added for {token_mint}")
        except Exception as e:
            log_event(f"[OpenPositions] Failed to add mention watch {token_mint}: {e}")

    async def _ensure_account_watches(self, accounts: Set[str]):
        added = 0
        for acc in accounts:
            if acc not in self.stream_subscriptions["accounts"]:
                self.stream_subscriptions["accounts"].append(acc)
                added += 1
            try:
                await request_account_watch(acc)
            except Exception as e:
                log_event(f"[OpenPositions] Failed to add account watch {acc}: {e}")
        if added:
            self.save_stream_subs()
            log_event(f"🛰️ Stream: +{added} LP accounts added to watchlist")

    def _merge_lp_accounts_persisted(self, token: str, lp_accounts: Set[str]):
        # Merge LP accounts into any wallet that holds this token in 'holding' state
        changed = False
        for wallet, tokens in self.positions.items():
            if token in tokens and tokens[token].get("status") == "holding":
                cur = set(tokens[token].get("lp_accounts") or [])
                new = cur | set(lp_accounts or [])
                if new != cur:
                    tokens[token]["lp_accounts"] = sorted(list(new))
                    changed = True
        if changed:
            self.save_positions()

        # Also mirror into stream_subscriptions file
        merged = set(self.stream_subscriptions.get("accounts", [])) | set(lp_accounts or [])
        self.stream_subscriptions["accounts"] = sorted(list(merged))
        self.save_stream_subs()

    async def _remove_watches_for_token(self, token: str, lp_accounts: Set[str]):
        # remove mint mention
        try:
            if token in self.stream_subscriptions["mentions"]:
                self.stream_subscriptions["mentions"].remove(token)
                self.save_stream_subs()
            await stop_logs_mention_watch(token)
            log_event(f"🗑 Stream: mention watch removed for {token}")
        except Exception as e:
            log_event(f"[OpenPositions] Failed to remove mention watch {token}: {e}")

        # remove LP accounts
        removed = 0
        for acc in lp_accounts or []:
            try:
                if acc in self.stream_subscriptions["accounts"]:
                    self.stream_subscriptions["accounts"].remove(acc)
                    removed += 1
                await stop_account_watch(acc)
            except Exception as e:
                log_event(f"[OpenPositions] Failed to remove account watch {acc}: {e}")
        if removed:
            self.save_stream_subs()
            log_event(f"🗑 Stream: -{removed} LP accounts removed for {token}")

    async def _replay_persisted_stream_subs(self):
        """
        If stream_subscriptions.json contains previous watches, replay them to the live stream.
        """
        # Mentions
        for pk in self.stream_subscriptions.get("mentions", []):
            try:
                await request_logs_mention_watch(pk)
            except Exception as e:
                log_event(f"[OpenPositions] Replay mention failed {pk}: {e}")
        # Accounts
        for acc in self.stream_subscriptions.get("accounts", []):
            try:
                await request_account_watch(acc)
            except Exception as e:
                log_event(f"[OpenPositions] Replay account failed {acc}: {e}")


def _iter_pool_records(pools) -> List[dict]:
    if isinstance(pools, dict):
        # possible format: {"pools":[{...},{...}]}
        if "pools" in pools and isinstance(pools["pools"], list):
            return pools["pools"]
        # or a map of id->pool
        return list(pools.values())
    elif isinstance(pools, list):
        return pools
    return []


async def _maybe_await(x):
    if asyncio.iscoroutine(x):
        return await x
    return x


open_position_tracker = OpenPositionTracker()
