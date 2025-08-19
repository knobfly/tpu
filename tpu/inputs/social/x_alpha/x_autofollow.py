import asyncio
import time
from typing import Iterable, Optional

import aiohttp
from core.live_config import config
from inputs.social.x_alpha.alpha_account_tracker import alpha_account_tracker
from utils.logger import log_event

# --- Config & Auth -----------------------------------------------------------

APP_BEARER = config.get("twitter_bearer_token")              # App-only (read-only)
USER_ACCESS = config.get("twitter_user_access_token")        # REQUIRED for follow/unfollow

READ_HEADERS = {
    "Authorization": f"Bearer {APP_BEARER}" if APP_BEARER else "",
    "Content-Type": "application/json",
}

USER_HEADERS = {
    "Authorization": f"Bearer {USER_ACCESS}" if USER_ACCESS else "",
    "Content-Type": "application/json",
}

# --- Twitter v2 endpoints ----------------------------------------------------

API_ME           = "https://api.twitter.com/2/users/me"
API_USER_BY_NAME = "https://api.twitter.com/2/users/by/username/{handle}"
API_FOLLOW       = "https://api.twitter.com/2/users/{source_id}/following"
API_UNFOLLOW     = "https://api.twitter.com/2/users/{source_id}/following/{target_id}"

# --- Local policy knobs ------------------------------------------------------

FOLLOW_COOLDOWN_SECONDS = int(config.get("x_autofollow_cooldown_s", 60))
FOLLOW_SCORE_THRESHOLD  = int(config.get("x_autofollow_score_threshold", 70))  # % from alpha_account_tracker

# --- State ------------------------------------------------------------------

followed_accounts: set[str] = set()
_last_follow_time = 0.0


# === Helpers =================================================================

def _norm_handle(s: str) -> str:
    s = (s or "").strip()
    return s[1:] if s.startswith("@") else s

def _is_user_id(s: str) -> bool:
    # Twitter v2 numeric user IDs
    return s.isdigit()

def _can_follow_now() -> bool:
    return (time.monotonic() - _last_follow_time) >= FOLLOW_COOLDOWN_SECONDS

async def _get_own_user_id(session: aiohttp.ClientSession) -> Optional[str]:
    if not USER_ACCESS:
        log_event("🚫 twitter_user_access_token missing — follow/unfollow disabled.")
        return None
    try:
        async with session.get(API_ME, headers=USER_HEADERS, timeout=15) as r:
            if r.status != 200:
                txt = await r.text()
                log_event(f"❌ /users/me failed ({r.status}): {txt}")
                return None
            data = await r.json()
            return (data.get("data") or {}).get("id")
    except Exception as e:
        log_event(f"❌ /users/me error: {e}")
        return None

async def _resolve_user_id(session: aiohttp.ClientSession, handle_or_id: str) -> Optional[str]:
    if _is_user_id(handle_or_id):
        return handle_or_id
    handle = _norm_handle(handle_or_id)
    if not handle:
        return None
    try:
        if not APP_BEARER:
            log_event("⚠️ twitter_bearer_token missing; cannot lookup username.")
            return None
        url = API_USER_BY_NAME.format(handle=handle)
        async with session.get(url, headers=READ_HEADERS, timeout=15) as r:
            if r.status != 200:
                txt = await r.text()
                log_event(f"❌ Lookup @{handle} failed ({r.status}): {txt}")
                return None
            data = await r.json()
        return (data.get("data") or {}).get("id")
    except Exception as e:
        log_event(f"❌ Lookup error for @{handle}: {e}")
        return None


# === Public API ==============================================================

async def follow_user(user_id_or_handle: str) -> bool:
    """
    Follow a user by @handle or numeric user ID.
    Requires config.twitter_user_access_token (user-context OAuth2).
    Applies cooldown & score threshold via alpha_account_tracker.
    """
    global _last_follow_time

    if not config.get("x_autofollow_enabled", True):
        log_event("🚫 Auto-follow disabled in config.")
        return False

    # Cooldown
    if not _can_follow_now():
        return False

    # Score gate (only when we have a handle)
    handle_for_score = None if _is_user_id(user_id_or_handle) else _norm_handle(user_id_or_handle)
    if handle_for_score:
        score = alpha_account_tracker.get_score(handle_for_score)
        if score < FOLLOW_SCORE_THRESHOLD:
            log_event(f"⚠️ Skip follow @{handle_for_score} — score {score}% < {FOLLOW_SCORE_THRESHOLD}%")
            return False

    async with aiohttp.ClientSession() as session:
        target_id = await _resolve_user_id(session, user_id_or_handle)
        if not target_id:
            log_event(f"⚠️ No user ID for {user_id_or_handle}")
            return False

        # Don’t re-follow
        if handle_for_score and handle_for_score in followed_accounts:
            return True

        source_id = await _get_own_user_id(session)
        if not source_id:
            return False  # logged already

        # POST /2/users/:source_id/following
        url = API_FOLLOW.format(source_id=source_id)
        payload = {"target_user_id": target_id}

        try:
            async with session.post(url, json=payload, headers=USER_HEADERS, timeout=20) as r:
                body = await r.text()
                if r.status in (200, 201):
                    if handle_for_score:
                        followed_accounts.add(handle_for_score)
                    _last_follow_time = time.monotonic()
                    who = f"@{handle_for_score}" if handle_for_score else target_id
                    log_event(f"✅ Followed {who}")
                    return True
                elif r.status == 429:
                    log_event(f"⏳ Rate limited following {user_id_or_handle}. Backing off.")
                    _last_follow_time = time.monotonic()  # still respect cooldown
                    return False
                else:
                    log_event(f"❌ Follow {user_id_or_handle} failed ({r.status}): {body}")
                    return False
        except Exception as e:
            log_event(f"❌ Follow error for {user_id_or_handle}: {e}")
            return False


async def unfollow_user(user_id_or_handle: str) -> bool:
    """
    Unfollow a user by @handle or numeric user ID.
    """
    async with aiohttp.ClientSession() as session:
        target_id = await _resolve_user_id(session, user_id_or_handle)
        if not target_id:
            log_event(f"⚠️ No user ID for {user_id_or_handle}")
            return False

        source_id = await _get_own_user_id(session)
        if not source_id:
            return False

        url = API_UNFOLLOW.format(source_id=source_id, target_id=target_id)
        try:
            async with session.delete(url, headers=USER_HEADERS, timeout=20) as r:
                body = await r.text()
                if r.status in (200, 201):
                    handle = _norm_handle(user_id_or_handle)
                    if handle:
                        followed_accounts.discard(handle)
                    log_event(f"✅ Unfollowed {user_id_or_handle}")
                    return True
                elif r.status == 429:
                    log_event(f"⏳ Rate limited unfollowing {user_id_or_handle}.")
                    return False
                else:
                    log_event(f"❌ Unfollow {user_id_or_handle} failed ({r.status}): {body}")
                    return False
        except Exception as e:
            log_event(f"❌ Unfollow error for {user_id_or_handle}: {e}")
            return False


async def ensure_following(handles_or_ids: Iterable[str]) -> None:
    """
    Utility for boot: follow a batch (e.g., toly, raj, key Solana feeds).
    """
    if not handles_or_ids:
        return
    for item in handles_or_ids:
        try:
            await follow_user(item)
        except Exception as e:
            log_event(f"[ensure_following] error for {item}: {e}")
        await asyncio.sleep(0.6)  # gentle pacing
