import json
import os
from collections import defaultdict
from datetime import datetime

TRACE_LOG = "/home/ubuntu/nyx/runtime/memory/strategy/weight_traces.json"
MAX_TRACE_LOG = 2500

def load_traces():
    if not os.path.exists(TRACE_LOG):
        return []
    try:
        with open(TRACE_LOG, "r") as f:
            return json.load(f)
    except:
        return []

def save_traces(traces):
    try:
        with open(TRACE_LOG, "w") as f:
            json.dump(traces[-MAX_TRACE_LOG:], f, indent=2)
    except:
        pass

def log_weight_trace(token_address: str, final_score: float, action: str, breakdown: dict, reasoning: list, mode: str = "snipe"):
    traces = load_traces()
    trace = {
        "token": token_address,
        "score": final_score,
        "action": action,
        "mode": mode,
        "reasoning": reasoning,
        "breakdown": breakdown,
        "timestamp": datetime.utcnow().isoformat()
    }
    traces.append(trace)
    save_traces(traces)

def get_trace_summary(token_address: str):
    traces = load_traces()
    for entry in reversed(traces):
        if entry["token"] == token_address:
            return entry
    return None

def summarize_trace_insights():
    traces = load_traces()
    tag_counts = defaultdict(int)
    source_weights = defaultdict(list)

    for entry in traces:
        for reason in entry.get("reasoning", []):
            tag_counts[reason] += 1
        for source, value in entry.get("breakdown", {}).items():
            source_weights[source].append(value)

    top_sources = sorted(source_weights.items(), key=lambda x: -sum(map(abs, x[1])))[:10]
    return {
        "top_reason_tags": sorted(tag_counts.items(), key=lambda x: -x[1])[:10],
        "top_weight_sources": [
            (src, round(sum(vals)/len(vals), 2)) for src, vals in top_sources
        ]
    }
