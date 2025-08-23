import json
import os
from utils.logger import log_event
from utils.rpc_loader import get_random_rpc

CONFIG_PATH = os.path.expanduser("home/ubuntu/nyx/config.json")

DEFAULT_CONFIG = {
    "wallet_dir": "home/ubuntu/nyx/wallets",
    "use_multi_wallet": true,
    "telegram_token": "",
    "telegram_chat_id": "",
    "default_buy_amount": 0.1,
    "sell_target_pct": 30,
    "max_active_tokens": 5,
    "enable_trailing_stop": True,
    "trailing_stop_pct": 12,
    "auto_rebuy": True,
    "volume_filter_sol": 5.0,
    "blacklist": [],
    "suppress_warnings": False
}

def merge_with_defaults(user_config):
    for key, value in DEFAULT_CONFIG.items():
        if key not in user_config:
            user_config[key] = value
    return user_config

def load_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        print(f"✅ Created default config at {CONFIG_PATH}. Please update it.")

    print("✅ Loaded config path:", CONFIG_PATH)

    with open(CONFIG_PATH, 'r') as f:
        user_config = json.load(f)

    config = merge_with_defaults(user_config)

    token = config.get("telegram_token", "").strip()
    chat_id = config.get("telegram_chat_id", "").strip()
    config["telegram_token"] = token
    config["telegram_chat_id"] = chat_id

    # ✅ Inject randomized RPC
    config["rpc_endpoint"] = get_random_rpc("solana")

    if not token or not token.startswith("789"):
        if not config.get("suppress_warnings", False):
            log_event("⚠️ Telegram bot token missing or invalid.")
    else:
        log_event("✅ Telegram token loaded successfully.")

    return config

