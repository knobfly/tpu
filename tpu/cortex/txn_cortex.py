class TxnCortex:
    # --- Supervisor Integration Hooks ---
    def receive_chart_signal(self, token_context: dict, chart_insights: dict):
        """
        Receive chart signals broadcast from supervisor or other modules.
        Can be used to update transaction analytics or trigger scoring.
        """
        pass

    def update_persona_context(self, persona_context: dict):
        """
        Receive persona context updates for adaptive transaction logic.
        """
        pass

    def receive_analytics_update(self, update: dict):
        """
        Receive analytics/state updates for unified decision-making.
        """
        pass

    def contribute_features(self, token_context: dict) -> dict:
        """
        Contribute transaction-derived features for cross-module analytics.
        """
        # Example: Use analyze_transactions to extract features
        # This assumes you have access to recent_txns and required functions
        # For now, returns empty dict (to be implemented with real context)
        return {}
    def __init__(self, memory):
        self.memory = memory

    def analyze_transactions(
        self,
        token_data: dict,
        recent_txns: list,
        check_lp_add_event,
        detect_sniper_behavior,
        classify_pump_pattern,
        detect_rug_signature,
        get_influential_wallet_trigger,
        classify_txn_events,
        summarize_event_activity,
        update_token_txn_memory
    ) -> dict:
        token_address = token_data.get("token_address")
        token_name = token_data.get("token_name", "unknown")
        mode = token_data.get("mode", "trade")

        if not recent_txns:
            return {
                "lp_added": False,
                "sniper_pressure": 0,
                "early_buy_window": False,
                "txn_score": 0,
                "rug_flagged": False,
                "influence_score": 0,
                "event_tags": [],
                "event_summary": ""
            }

        lp_event = check_lp_add_event(token_address, recent_txns)
        sniper_data = detect_sniper_behavior(recent_txns)
        sniper_pressure = sniper_data.get("intensity", 0)
        early_window = sniper_data.get("window_open", False)

        pump_score = 0
        pump_type = "unknown"
        if mode == "trade":
            try:
                pump_profile = classify_pump_pattern(token_address, recent_txns)
                pump_score = pump_profile.get("score", 0)
                pump_type = pump_profile.get("type", "unknown")
            except:
                pass

        try:
            rug_risk = detect_rug_signature(recent_txns)
            rug_flagged = rug_risk.get("flagged", False)
        except:
            rug_flagged = False

        try:
            influence_score = get_influential_wallet_trigger(recent_txns)
        except:
            influence_score = 0

        try:
            event_tags = classify_txn_events(recent_txns)
        except:
            event_tags = []

        try:
            event_summary = summarize_event_activity(recent_txns)
        except:
            event_summary = ""

        txn_score = 0
        if lp_event:
            txn_score += 10
        txn_score += sniper_pressure
        txn_score += pump_score
        txn_score += influence_score
        if rug_flagged:
            txn_score -= 8

        try:
            update_token_txn_memory(token_address, {
                "token_name": token_name,
                "lp_event": lp_event,
                "early_buy_window": early_window,
                "sniper_pressure": sniper_pressure,
                "pump_type": pump_type,
                "txn_score": txn_score,
                "rug_flagged": rug_flagged,
                "influence_score": influence_score,
                "event_tags": event_tags,
                "event_summary": event_summary
            })
        except:
            pass

        return {
            "lp_added": lp_event,
            "sniper_pressure": sniper_pressure,
            "early_buy_window": early_window,
            "txn_score": round(txn_score, 2),
            "rug_flagged": rug_flagged,
            "influence_score": influence_score,
            "event_tags": event_tags,
            "event_summary": event_summary
        }

def evaluate_junk_confidence(token: str, metadata: dict, raw_score: float) -> float:
    """
    Simple heuristic to decide if a token is likely junk.
    Returns a confidence score (0.0 to 10.0).
    """
    holders = metadata.get("holders", 0)
    name = metadata.get("name", "").lower()
    symbol = metadata.get("symbol", "").lower()

    penalty = 0
    if any(x in name for x in ["test", "dev", "rug", "airdrop", "scam"]):
        penalty += 2
    if any(x in symbol for x in ["???", "rugg", "xxx", "wtf"]):
        penalty += 1
    if holders < 5:
        penalty += 2
    if raw_score < 1.0:
        penalty += 2

    confidence = max(0.0, 10.0 - penalty)
    return confidence

def evaluate_cluster_confidence(token: str, cluster_data: dict, raw_score: float = 0.0) -> float:
    """
    Estimate the confidence level of a cluster-based signal.
    Returns float between 0.0 and 10.0
    """
    wallets = cluster_data.get("wallets", [])
    total_vol = sum(w.get("amount_usd", 0.0) for w in wallets)
    num_wallets = len(wallets)

    base = 3.0 if num_wallets >= 2 else 1.0
    if total_vol > 5000:
        base += 2
    if any("alpha" in (w.get("tag", "") or "").lower() for w in wallets):
        base += 2
    if raw_score > 3.0:
        base += 1

    return min(10.0, base)

def register_buy(token: str, wallet: str, tx: dict):
    """
    Record a buy action in memory/logs for follow-up analysis.
    Used after buying a token to track future outcome.

    Args:
        token (str): The token address or ID.
        wallet (str): The wallet that performed the buy.
        tx (dict): The raw transaction info (timestamp, amount, signature, etc).
    """
    from librarian.data_librarian import librarian

    try:
        event = {
            "token": token,
            "wallet": wallet,
            "action": "buy",
            "amount": tx.get("amount"),
            "timestamp": tx.get("timestamp") or tx.get("time"),
            "signature": tx.get("signature"),
            "price_usd": tx.get("price_usd"),
            "volume_usd": tx.get("volume_usd"),
            "reasoning": tx.get("reasoning", []),
        }
        librarian.log_recent_trade(event)
        librarian.mark_token_recently_bought(token, wallet)
    except Exception as e:
        from utils.logger import log_event
        log_event(f"[txn_cortex] Failed to register buy: {e}")

def register_buy_interest(token: str, reason: str = "scored", score: float = 0.0, wallet: str = None):
    """
    Log interest in buying a token without actually buying yet.
    Helps track AI intent vs actual executions for learning.

    Args:
        token (str): Token address
        reason (str): Why this token was interesting (e.g., "sniper", "volume_spike")
        score (float): Confidence or AI score
        wallet (str): Optional wallet ID (if pre-assigned)
    """
    from librarian.data_librarian import librarian

    try:
        event = {
            "token": token,
            "action": "interest",
            "reason": reason,
            "score": score,
            "wallet": wallet,
            "timestamp": time.time()
        }
        librarian.log_recent_trade(event)
        librarian.mark_token_interested(token, wallet=wallet, reason=reason)
    except Exception as e:
        from utils.logger import log_event
        log_event(f"[txn_cortex] Failed to register buy interest: {e}")
