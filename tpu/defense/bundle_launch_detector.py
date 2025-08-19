# modules/bundle_launch_detector.py

import logging
from datetime import datetime, timedelta

from utils.logger import log_event
from utils.rpc_loader import get_active_rpc  # ✅ Use rpc_router
from utils.service_status import update_status


# === Smart Bundle Launch Detector ===
async def detect_bundle_launch(token_address: str) -> dict:
    update_status("bundle_launch_detector")

    try:
        # Fetch initial token holders
        holders_data = await get_active_rpc(
            f"/tokens/holders?mint={token_address}&page=1&limit=25",
            priority="high"
        )
        holders = holders_data.get("holders", [])

        top_wallets = [h.get("owner") for h in holders if h.get("owner")]
        if not top_wallets:
            return {"bundle_detected": False, "reason": "No holders found"}

        # Fetch transaction history for the token itself
        txs = await get_active_rpc(
            f"/addresses/{token_address}/transactions?limit=50",
            priority="high"
        )

        if not txs:
            return {"bundle_detected": False, "reason": "No transactions found"}

        launch_time = datetime.utcnow()
        first_tx_time = None
        first_txs = []

        for tx in txs:
            ts = tx.get("timestamp")
            if not ts:
                continue
            tx_time = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")
            if not first_tx_time or tx_time < first_tx_time:
                first_tx_time = tx_time
            first_txs.append({
                "signature": tx.get("signature"),
                "timestamp": tx_time,
                "events": tx.get("events", {})
            })

        # Look for bundle pattern (no transfers to holders at launch)
        bundle_detected = True
        for wallet in top_wallets:
            wallet_txs = await get_active_rpc(
                f"/addresses/{wallet}/transactions?limit=25",
                priority="low"
            )
            if not wallet_txs:
                continue
            received_at_launch = False
            for tx in wallet_txs:
                ts = tx.get("timestamp")
                if not ts:
                    continue
                tx_time = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")
                if tx_time <= first_tx_time + timedelta(seconds=10):
                    received_at_launch = True
                    break
            if received_at_launch:
                bundle_detected = False
                break

        result = {
            "bundle_detected": bundle_detected,
            "launch_time": first_tx_time.isoformat() if first_tx_time else None,
            "holders_checked": len(top_wallets),
            "reason": "No top holders received tokens during launch window"
        }

        if bundle_detected:
            log_event(f"[BundleDetector] 🚨 Bundle launch detected for {token_address}")
        else:
            log_event(f"[BundleDetector] ✅ No bundle pattern for {token_address}")

        return result

    except Exception as e:
        logging.warning(f"[BundleDetector] Error detecting bundle launch: {e}")
        return {"bundle_detected": False, "reason": "Error or fallback"}
