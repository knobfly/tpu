# cortex/signal_aggregate.py
from core.live_config import config

WEIGHTS = {
    "meta": 0.35,          # liquidity/mcap/holders/age/etc.
    "group_rep": 0.15,     # group reputation boost/penalty
    "sentiment": 0.20,     # rolling sentiment window
    "history": 0.15,       # your own win/loss/profit memory
    "social_intensity": 0.10,  # recent mention velocity
    "sniper": 0.05,        # patterns
}

VETOES = {
    "dust": True,
    "blacklisted": True,
    "min_liquidity": 3_000,     # example
}

def aggregate_features(signal, meta, group_score, senti_score, mem_stats,
                       mention_velocity, sniper_signal) -> dict:
    # Normalize each component to [0..1]
    f_meta   = meta.get("quality", 0.0)                 # you define
    f_group  = 0.5 + max(-0.5, min(0.5, group_score/20))
    f_senti  = (senti_score + 3) / 6.0                  # if your senti is [-3..+3]
    f_hist   = normalize_history(mem_stats)             # turn wins/loss/profit into 0..1
    f_intens = normalize_velocity(mention_velocity)     # 0..1
    f_snipe  = sniper_signal or 0.0

    raw = (
        WEIGHTS["meta"]            * f_meta   +
        WEIGHTS["group_rep"]       * f_group  +
        WEIGHTS["sentiment"]       * f_senti  +
        WEIGHTS["history"]         * f_hist   +
        WEIGHTS["social_intensity"]* f_intens +
        WEIGHTS["sniper"]          * f_snipe
    )

    return {
        "score": max(0.0, min(1.0, raw)),
        "features": {
            "meta": f_meta, "group": f_group, "sentiment": f_senti,
            "history": f_hist, "intensity": f_intens, "sniper": f_snipe
        }
    }

def should_veto(meta, blacklisted: bool) -> tuple[bool, str|None]:
    if blacklisted and VETOES["blacklisted"]:
        return True, "blacklisted"
    if VETOES["dust"] and meta.get("is_dust"):
        return True, "dust_token"
    if meta.get("liquidity", 0) < VETOES["min_liquidity"]:
        return True, "low_liquidity"
    return False, None
