# inputs/social/x_alpha/x_alpha_brain_adapter.py
from __future__ import annotations

import inspect
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core.live_config import config
from core.llm.style_evolution import style_evolution
from inputs.social.x_alpha.alpha_account_tracker import alpha_account_tracker
from inputs.social.x_alpha.alpha_post_manager import alpha_post_manager
# Import the module that exposes your brain (class/instance/singleton/getter)
from inputs.social.x_alpha import x_alpha_brain as _brain_mod
from inputs.social.x_alpha.x_behavior_filter import is_safe_to_reply
from inputs.social.x_alpha.x_post_engine import post_quote, post_reply
from langdetect import LangDetectException, detect
from librarian.data_librarian import librarian
from utils.logger import log_event
from utils.token_utils import extract_token_name

_logger = logging.getLogger("AlphaBrainAdapter")

# ---------------------------------------------------------------------------
# Backoff / cooldown knobs
# ---------------------------------------------------------------------------
post_failure_count = 0
last_post_time = 0.0
backoff_until = 0.0

POST_COOLDOWN_SECONDS = int(config.get("x_post_cooldown_sec", 60))  # default: 60s
BACKOFF_THRESHOLD = int(config.get("x_backoff_fail_threshold", 3))  # failures before backoff
BACKOFF_DURATION = int(config.get("x_backoff_duration_sec", 3600))  # default: 1 hour

# ---------------------------------------------------------------------------
# Brain resolution + compatibility shim
# ---------------------------------------------------------------------------

def _resolve_brain(explicit: Optional[Any] = None) -> Any:
    """
    Return a usable brain instance from:
      - explicit instance (passed in)
      - module attribute: .brain / .singleton / .default / .BRAIN
      - module getter:    .instance() / .get() / .get_instance()
      - class in module:  XAlphaBrain / Brain / AlphaBrain
      - module acting like instance (has analyze_post)
    """
    if explicit is not None:
        return explicit

    # Ready-made instance on module?
    for attr in ("brain", "singleton", "default", "BRAIN"):
        b = getattr(_brain_mod, attr, None)
        if b is not None and not inspect.ismodule(b):
            return b

    # Getter on module?
    for getter in ("instance", "get", "get_instance"):
        g = getattr(_brain_mod, getter, None)
        if callable(g):
            try:
                return g()
            except Exception as e:
                _logger.warning(f"[XAlphaBrainAdapter] {_brain_mod.__name__}.{getter}() failed: {e}")

    # Construct from a known class name?
    for clsname in ("XAlphaBrain", "Brain", "AlphaBrain"):
        C = getattr(_brain_mod, clsname, None)
        if inspect.isclass(C):
            try:
                return C()
            except Exception as e:
                _logger.warning(f"[XAlphaBrainAdapter] {clsname}() ctor failed: {e}")

    # Module itself exposing analyze_post?
    if hasattr(_brain_mod, "analyze_post"):
        return _brain_mod

    raise RuntimeError("Could not resolve an XAlphaBrain instance")

def _call_analyze_post(
    brain: Any,
    *,
    handle: Optional[str],
    token: Optional[str],
    text: Optional[str],
    content: Optional[str],
    meta: Optional[dict],
) -> str:
    """
    Try multiple signatures to support old/new brain APIs.
    Returns an action string like 'quote'|'reply'|'watch'|'alert'|'ignore'.
    """
    meta = meta or {}
    payload_text = content if content is not None else text
    alt_text = text if text is not None else content

    fn = getattr(brain, "analyze_post", None)
    if not callable(fn):
        fn = getattr(brain, "analyze", None)
    if not callable(fn):
        raise AttributeError("Brain has no analyze_post/analyze method")

    trials = [
        # rich kwargs (newer shapes)
        lambda: fn(handle=handle, token=token, content=payload_text, meta=meta),
        lambda: fn(handle=handle, token=token, text=alt_text, meta=meta),

        # simpler kwargs
        lambda: fn(content=payload_text),
        lambda: fn(text=alt_text),

        # >>> add exact 3-positional signature (your brain’s current API)
        lambda: fn(handle, token, payload_text),

        # positional fallbacks (historical variants)
        lambda: fn(handle, token, alt_text, meta),      # may TypeError (4 args) — that’s fine
        lambda: fn(handle, payload_text, meta),
        lambda: fn(handle, alt_text, meta),
        lambda: fn(payload_text),
        lambda: fn(alt_text),
    ]

    last_err = None
    for attempt in trials:
        try:
            out = attempt()
            return out if isinstance(out, str) else str(out or "ignore")
        except TypeError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue

    raise TypeError(f"analyze_post compatible call failed: {last_err}")

def analyze_post_compat(
    *,
    handle: str,
    token: Optional[str],
    content: Optional[str] = None,
    text: Optional[str] = None,   # kept for backwards compatibility
    meta: Optional[dict] = None,
    brain: Optional[Any] = None,
    **kw,
) -> str:
    """
    Unified entry-point for scanners/orchestrators.
    - Resolves a brain instance (no 'module is not callable' crashes)
    - Accepts either `content` or `text`
    - Tries multiple method signatures on the brain
    """
    b = _resolve_brain(brain or kw.get("brain"))
    try:
        return _call_analyze_post(b, handle=handle, token=token, text=text, content=content, meta=meta)
    except Exception as e:
        log_event(f"⚠️ AI brain analyze_post failed: {e}")
        return "ignore"

# ---------------------------------------------------------------------------
# Backoff / cooldown helpers
# ---------------------------------------------------------------------------

def is_in_backoff() -> bool:
    return time.time() < backoff_until

def _enter_backoff():
    global backoff_until
    backoff_until = time.time() + BACKOFF_DURATION
    log_event(f"🚫 Backoff triggered for {BACKOFF_DURATION//60} minutes after repeated failures")

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def detect_language(text: str) -> str:
    try:
        return detect(text or "")  # langdetect
    except LangDetectException:
        return "unknown"

# ---------------------------------------------------------------------------
# Tweet handler (used by your pipelines)
# ---------------------------------------------------------------------------

async def handle_tweet(tweet: Dict[str, Any]):
    """
    Minimal end-to-end handler: language gate, safety check, cooldown/backoff,
    ask brain for action, then quote/reply if allowed.
    """
    global post_failure_count, last_post_time

    handle = str(tweet.get("user") or "").strip()
    token = tweet.get("token")
    text = tweet.get("text") or ""

    if not handle or not text:
        return
    if not token:
        # we still learn from it elsewhere; posting needs a token key
        return

    # Language gate
    allowed_langs = set(config.get("x_allowed_languages", ["en"]))
    detected_lang = detect_language(text)
    if allowed_langs and detected_lang not in allowed_langs:
        log_event(f"🌐 Skipped tweet from @{handle} due to unsupported language: {detected_lang}")
        return

    # Reply safety
    if not is_safe_to_reply(handle, text):
        log_event(f"❌ Unsafe to reply to @{handle}: skipped")
        return

    # Backoff mode
    if config.get("x_backoff_enabled", False) and is_in_backoff():
        log_event(f"🛑 In backoff mode: skipping post for {token}")
        return

    # Post cooldown
    if config.get("x_post_cooldowns", False):
        time_since_last = time.time() - float(last_post_time or 0.0)
        if time_since_last < POST_COOLDOWN_SECONDS:
            log_event(f"⏱️ Cooldown active: {time_since_last:.1f}s since last post")
            return

    # Brain decision (adapter handles API shape)
    try:
        decision = analyze_post_compat(handle=handle, token=token, content=text, meta={"tweet": tweet})
    except Exception as e:
        log_event(f"⚠️ analyze_post_compat failed: {e}")
        return

    success = False

    # Quote
    if decision == "quote" and config.get("x_quote_mode", True):
        try:
            await post_quote(token, text)
            success = True
        except Exception as e:
            log_event(f"❌ Quote post failed for {token}: {e}")

    # Reply
    elif decision == "reply" and config.get("x_autopost_enabled", True):
        try:
            await post_reply(token, handle, text)
            success = True
        except Exception as e:
            log_event(f"❌ Reply post failed for {token}: {e}")

    else:
        log_event(f"🤫 No post for {token} — decision={decision}")

    # Track result + backoff logic
    if success:
        alpha_post_manager.register_post(token, decision, "auto")
        alpha_account_tracker.register_post(handle, token, outcome="pending")
        post_failure_count = 0
        last_post_time = time.time()
        log_event(f"✅ Post successful for {token} from @{handle}")
    else:
        post_failure_count += 1
        if post_failure_count >= BACKOFF_THRESHOLD and config.get("x_backoff_enabled", False):
            _enter_backoff()

# ---------------------------------------------------------------------------
# Feedback into style evolution
# ---------------------------------------------------------------------------

def log_message_feedback(content: str, engagement: float = 0.0, sentiment: float = 0.0):
    style_evolution().record_message_feedback(
        engagement=engagement,    # 0..1
        sentiment=sentiment,      # -1..1
        length_tokens=len((content or "").split()),
        context="x",
    )

# ---------------------------------------------------------------------------
# Alpha overlap utility (unchanged terminology)
# ---------------------------------------------------------------------------

def detect_alpha_overlap(token: str, hours: int = 12) -> Dict[str, Any]:
    """
    Check if `token` has been mentioned by known alpha influencers or tracked smart sources recently.
    Returns: { overlap_count, matched_accounts, recent_mentions }
    """
    try:
        history = librarian.get_token_mention_history(token, hours=hours)
        overlap_accounts: List[str] = []
        mentions: List[Dict[str, Any]] = []

        for item in history or []:
            account = str(item.get("account", "") or "")
            if account.startswith("alpha_") or account.startswith("influencer_"):
                overlap_accounts.append(account)
                mentions.append(item)

        return {
            "overlap_count": len(overlap_accounts),
            "matched_accounts": overlap_accounts,
            "recent_mentions": mentions,
        }

    except Exception as e:
        _logger.warning(f"[AlphaBrainAdapter] Failed to check overlap for {token}: {e}")
        return {"overlap_count": 0, "matched_accounts": [], "recent_mentions": []}
