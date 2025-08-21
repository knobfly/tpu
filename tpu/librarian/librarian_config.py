# --- CONFIG SECTION ---
from pathlib import Path

# Root runtime folders (absolute path)
RUNTIME_ROOT = Path("/home/ubuntu/nyx/runtime")
LOGS_ROOT    = Path("/home/ubuntu/nyx/runtime/logs")
LIBRARY_ROOT = Path("/home/ubuntu/nyx/runtime/library")

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_RE = r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b"

GENRES = {
	"memes":    ["pepe", "wojak", "doge", "shib", "coq", "bonk", "meme"],
	"math":     ["arbitrage", "basis", "funding", "kelly", "pnl", "sharpe", "sortino", "variance", "alpha", "beta"],
	"profits":  ["win", "profit", "sell_win", "pnl_positive", "tp_hit"],
	"losses":   ["loss", "stop", "sell_loss", "rug", "pnl_negative"],
	"wallets":  ["wallet", "whale", "cluster", "cabal", "reputation", "banlist"],
	"listings": ["launch", "mint", "lp_add", "listing", "dex", "raydium", "orca", "pump"],
	"risk":     ["honeypot", "rug", "blacklist", "unlocked_lp", "scam"],
	"social":   ["telegram", "tweet", "x_post", "influencer", "sentiment"],
	"charts":   ["ohlcv", "pattern", "divergence", "volume_spike", "trend"],
}

# Known JSONL sources to tail & normalize
JSONL_SOURCES = {
	"insights":   RUNTIME_ROOT / "insights",
	"signals":    RUNTIME_ROOT / "signals",
	"trades":     RUNTIME_ROOT / "trades",
	"scoring":    RUNTIME_ROOT / "scoring",
	"wallets":    RUNTIME_ROOT / "wallets",
	"charts":     RUNTIME_ROOT / "charts",
	"firehose":   RUNTIME_ROOT / "firehose",
	"nft":        RUNTIME_ROOT / "nft",
	"strategy":   RUNTIME_ROOT / "strategy",
}

# Refresh intervals
DISK_SCAN_INTERVAL_SEC   = 5
STATUS_HEARTBEAT_SECONDS = 30

# Memory limits
MAX_EVENTS_PER_TYPE      = 5000     # global ring buffer per event type
MAX_TOKEN_EVENTS         = 2000     # per token
MAX_WALLET_EVENTS        = 2000     # per wallet
