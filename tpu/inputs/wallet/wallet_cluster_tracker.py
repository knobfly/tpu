import asyncio
import logging
import time
from collections import defaultdict

from core.live_config import config
from cortex.txn_cortex import evaluate_cluster_confidence, register_buy
from exec.trade_executor import TradeExecutor
from librarian.data_librarian import librarian
from scoring.scoring_engine import score_token
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status
from utils.token_utils import get_token_metadata
from utils.wallet_helpers import get_recent_buys

REFRESH_INTERVAL = 30
CLUSTER_WINDOW = 20
CLUSTER_MIN_WALLETS = 3
TRUSTED_WALLETS = config.get("trusted_wallets", [])


class WalletClusterTracker:
    def __init__(self):
        self.recent_buys = defaultdict(list)  # token → list of (wallet, timestamp)

    async def run(self):
        log_event("🤝 Wallet Cluster Tracker running...")
        while True:
            try:
                await self.detect_clusters()
            except Exception as e:
                logging.warning(f"[ClusterTracker] Error: {e}")
            await asyncio.sleep(REFRESH_INTERVAL)

    async def detect_clusters(self):
        update_status("wallet_cluster_tracker")
        buys = get_recent_buys(since_seconds=CLUSTER_WINDOW)
        now = time.time()

        for wallet, token in buys:
            if wallet not in TRUSTED_WALLETS:
                continue

            self.recent_buys[token].append((wallet, now))
            self.recent_buys[token] = [
                (w, t) for w, t in self.recent_buys[token] if now - t <= CLUSTER_WINDOW
            ]

            unique_wallets = {w for w, _ in self.recent_buys[token]}
            if len(unique_wallets) >= CLUSTER_MIN_WALLETS:
                self.recent_buys.pop(token)

                try:
                    metadata = await get_token_metadata(token)
                    if not metadata:
                        continue

                    score_result = await score_token(token, config)
                    score = float(score_result.get("score", 0))
                    confidence = evaluate_cluster_confidence(token, metadata, score)

                    log_event(
                        f"🤝 Cluster Buy Detected → {token} | Wallets: {len(unique_wallets)} | "
                        f"Score: {score:.2f} | Confidence: {confidence:.2f}"
                    )

                    log_scanner_insight(
                        token=token,
                        sentiment=0.0,
                        volume=len(unique_wallets),
                        result="cluster_detected"
                    )

                    if confidence >= config.get("cluster_conf_threshold", 6.5):
                        librarian.mark_token_origin(token, "wallet_cluster")
                        tx = TradeExecutor.buy_token(token, metadata, score, source="wallet_cluster")
                        register_buy(token, wallet=None, tx=tx)
                except Exception as e:
                    logging.warning(f"[ClusterTracker] Failed to process {token}: {e}")
