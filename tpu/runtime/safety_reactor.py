# runtime/safety_reactor.py
import logging

from runtime.event_bus import event_bus
from utils.logger import log_event
from utils.token_utils import add_to_blacklist


async def _handler(evt):
    t = evt.get("type")
    token = evt.get("token")
    if t == "lp_unlock":
        log_event(f"🔓 LP unlock detected for {token}")
        add_to_blacklist(token)
    elif t == "vault_drain":
        log_event(f"🚨 Vault drain for {token} (drop≈{evt.get('drop_pct'):.1f}%)")
    elif t == "honeypot_detected":
        log_event(f"🕳️ Honeypot detected for {token}")
        add_to_blacklist(token)

def register_safety_reactor():
    event_bus().subscribe(_handler)
