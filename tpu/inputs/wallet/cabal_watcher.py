import asyncio
import logging
from datetime import datetime

from inputs.wallet.wallet_cluster_analyzer import get_clusters
from librarian.data_librarian import librarian
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status
from utils.universal_input_validator import coerce_to_dict
from utils.wallet_tracker import get_wallet_token_activity

CABAL_TRIGGER_COUNT = 3  # Minimum wallet overlap to trigger alert
SEEN_TOKENS = set()

async def run_cabal_watcher(interval: int = 60):
    update_status("cabal_watcher")
    log_event("🧠 Cabal Watcher active.")

    while True:
        try:
            update_status("cabal_watcher")

            try:
                clusters = get_clusters()
            except TypeError:
                clusters = librarian.load_json_file("/home/ubuntu/nyx/runtime/cabal/clusters.json") or {}

            clusters = coerce_to_dict(clusters)
            if not isinstance(clusters, dict):
                log_event(f"⚠️ [CabalWatcher] Expected dict, got {type(clusters).__name__}")
                await asyncio.sleep(interval)
                continue

            for cluster_id, wallet_list in clusters.items():
                token_counts = {}

                for wallet in wallet_list:
                    tokens = await get_wallet_token_activity(wallet)
                    for token in tokens:
                        token_counts[token] = token_counts.get(token, 0) + 1

                for token, count in token_counts.items():
                    if count >= CABAL_TRIGGER_COUNT and token not in SEEN_TOKENS:
                        SEEN_TOKENS.add(token)

                        log_event(f"👥 Cabal detected entering {token} | {count} wallet overlap.")

                        # Tag + record in librarian
                        librarian.tag_token(token, "cabal_overlap")
                        librarian.record_signal({
                            "token": token,
                            "source": "cabal_watcher",
                            "confidence": 0.9,
                            "cluster_overlap": count,
                            "timestamp": datetime.utcnow().timestamp()
                        })

                        log_scanner_insight("cabal", {
                            "token": token,
                            "confidence": 0.9,
                            "cluster_overlap": count,
                            "timestamp": datetime.utcnow().isoformat()
                        })

                        if hasattr(librarian, "tg") and librarian.tg:
                            await librarian.tg.send_message(
                                f"🧠 *Cabal Alert:*\n"
                                f"Elite wallet cluster just entered `{token}`.\n"
                                f"Overlap: `{count}` wallets\n"
                                f"Nyx has marked this as high-confidence alpha."
                            )

        except Exception as e:
            logging.warning(f"[CabalWatcher] Error: {e}")

        await asyncio.sleep(interval)
