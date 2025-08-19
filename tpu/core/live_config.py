import json
import logging
import os

CONFIG_PATH = "/home/ubuntu/nyx/config.json"

# === Default Configuration for Nyx v2.0 ===
DEFAULT_CONFIG = {
    # === Core Settings ===
    "telegram_token": "",
    "telegram_chat_id": "",
    "reset_code": "0000",
    "mode": "balanced",

    # === AI / Strategy Toggles ===
    "ai_strategy": True,
    "auto_start": False,
    "auto_sell_mode": True,
    "wallet_rebalance": True,
    "lp_filter": True,
    "rebuy_on_dip": False,
    "contextual_holding": True,
    "force_scalp_mode": False,
    "time_stop_loss": False,

    # === Learning / LLM ===
    "enable_telegram_learning": True,
    "enable_telegram_talking": False,
    "enable_twitter_learning": True,
    "foreign_language_mode": False,
    "allow_public_alpha": True,
    "debug_mode": False,

    # === Risk + Protection ===
    "use_race_protection": True,
    "auto_blacklist_rug": True,
    "sniper_defender_enabled": True,
    "junk_token_cleaner": True,

    # === X (Twitter) Behavior ===
    "x_autopost_enabled": True,
    "x_autofollow_enabled": False,
    "x_backoff_enabled": True,
    "x_english_only": True,
    "x_quote_mode": False,
    "x_post_cooldowns": True,

    # === Firehose / Data Feeds ===
    "firehose_enabled": False,
    "heartbeat_alerts": True,
    "manual_override": False,

    # === Wallet Settings ===
    "nft_buy_ceiling": 1.0,
    "wallet_keypair": "",

    # === Memory / Logs ===
    "memory_trim_enabled": True,

    # === Live Runtime Toggles (new additions) ===
    "enable_sniping": True,
    "enable_autosell": True,
    "enable_rug_defense": True,
    "enable_ai_tuning": True,
    "enable_strategy_rotation": True,
    "enable_wallet_tracking": True,
    "enable_liquidity_watcher": True,
    "enable_sentiment_scoring": True,
    "enable_alpha_replies": True,
    "enable_firehose_mode": False,
    "enable_rpc_fallback": True,
    "max_daily_trades": 500,
    "manual_override_mode": False,
    "risk_level": "balanced",


    "split_order_enabled": True,
    "split_order_threshold_sol": 1.0,
    "split_order_pause_s": 1.2,
    "max_price_impact_pct": 0.12,
    "size_mult_min": 0.2,
    "size_mult_max": 1.2,
    "max_wallet_risk_pct": 0.5,
    "min_notional_sol": 0.01,
    "max_notional_sol": 2.0,

    "stream_safety": {
      "window_seconds": 60,
      "vault_drain_medium_pct": 0.25,
      "vault_drain_high_pct": 0.45,
      "min_vault_notional": 0.5,
      "realert_cooldown_s": 90
    }


}

config = {}

def load_config():
    """Load config.json or create with defaults."""
    global config
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                config = json.load(f)
        else:
            config = DEFAULT_CONFIG.copy()
            save_config()
            logging.info("⚙️ Created default config.json")

        # Ensure all keys exist (backward compatibility)
        updated = False
        for k, v in DEFAULT_CONFIG.items():
            if k not in config:
                config[k] = v
                updated = True
        if updated:
            save_config()

    except Exception as e:
        logging.error(f"❌ Failed to load live config: {e}")
        config = DEFAULT_CONFIG.copy()

def save_config():
    """Write current config to disk."""
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        logging.info("✅ Live config saved.")
    except Exception as e:
        logging.error(f"❌ Failed to save live config: {e}")

# Initialize at import
load_config()

