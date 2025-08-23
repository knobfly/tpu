# /graph_store.py
# Phase 8 — Memory & Knowledge Graph
# Tracks tokens, wallets, groups, traits, strategies, and events as nodes with rich relationships.

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import networkx as nx
except ImportError:
    raise ImportError("Please install networkx: pip install networkx")

GRAPH_PATH = "~/nyx/runtime/data/knowledge_graph.json"
LOCK = threading.RLock()


class MemoryGraph:
    def __init__(self, path: str = GRAPH_PATH):
        self.path = path
        self.graph = nx.DiGraph()
        self._load()

    # === Core Node/Edge Management ===
    def add_node(self, node_id: str, node_type: str, **attributes):
        with LOCK:
            if not self.graph.has_node(node_id):
                self.graph.add_node(node_id, type=node_type, created=time.time(), **attributes)
            else:
                self.graph.nodes[node_id].update(attributes)

    def add_edge(self, src: str, dst: str, relation: str, **attributes):
        with LOCK:
            self.graph.add_edge(src, dst, relation=relation, timestamp=time.time(), **attributes)

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        with LOCK:
            return self.graph.nodes[node_id] if self.graph.has_node(node_id) else None

    def get_neighbors(self, node_id: str) -> List[str]:
        with LOCK:
            return list(self.graph.neighbors(node_id)) if self.graph.has_node(node_id) else []

    def get_edges(self, node_id: Optional[str] = None) -> List[Tuple[str, str, Dict]]:
        with LOCK:
            if node_id and self.graph.has_node(node_id):
                return [(src, dst, self.graph[src][dst]) for src, dst in self.graph.edges(node_id)]
            else:
                return [(src, dst, self.graph[src][dst]) for src, dst in self.graph.edges()]

    def find_nodes_by_type(self, node_type: str) -> List[str]:
        with LOCK:
            return [n for n, attr in self.graph.nodes(data=True) if attr.get("type") == node_type]

    # === Knowledge Updates ===
    def record_wallet_trade(self, wallet: str, token: str, action: str, pnl: Optional[float] = None):
        self.add_node(wallet, "wallet")
        self.add_node(token, "token")
        self.add_edge(wallet, token, f"{action}_token", pnl=pnl)

    def record_group_mention(self, group: str, token: str):
        self.add_node(group, "group")
        self.add_node(token, "token")
        self.add_edge(group, token, "mentions")

    def record_strategy_result(self, strategy: str, token: str, outcome: str):
        self.add_node(strategy, "strategy")
        self.add_node(token, "token")
        self.add_edge(strategy, token, "outcome", result=outcome)

    def record_trait_link(self, token: str, trait: str, weight: float):
        self.add_node(token, "token")
        self.add_node(trait, "trait")
        self.add_edge(token, trait, "has_trait", weight=weight)

    # === Persistence ===
    def save(self):
        with LOCK:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                data = nx.node_link_data(self.graph)
                tmp = self.path + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, self.path)
            except Exception as e:
                logging.warning(f"[MemoryGraph] Failed to save graph: {e}")

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    data = json.load(f)
                self.graph = nx.node_link_graph(data)
            except Exception as e:
                logging.warning(f"[MemoryGraph] Failed to load graph: {e}")

    # === Analysis / Queries ===
    def top_wallets_by_activity(self, top_n: int = 5) -> List[Tuple[str, int]]:
        wallets = self.find_nodes_by_type("wallet")
        activity = [(w, len(self.get_neighbors(w))) for w in wallets]
        return sorted(activity, key=lambda x: x[1], reverse=True)[:top_n]

    def get_tokens_linked_to_wallet(self, wallet: str) -> List[str]:
        return self.get_neighbors(wallet)

    def find_influential_wallets(self, min_mentions: int = 3) -> List[str]:
        wallets = self.find_nodes_by_type("wallet")
        return [w for w in wallets if len(self.get_neighbors(w)) >= min_mentions]

    def get_token_traits(self, token: str) -> List[str]:
        neighbors = self.get_neighbors(token)
        return [n for n in neighbors if self.graph.nodes[n].get("type") == "trait"]
        # === Influence Scoring ===

    def pagerank_influence(self, node_type: str = "wallet", alpha: float = 0.85) -> dict:
        """
        Computes PageRank scores for nodes of a given type (wallet, token, group, etc.)
        Returns: {node_id: score}
        """
        from utils.graph_influence import pagerank_influence
        # Build edge list for relevant nodes
        edges = []
        for src, dst, attrs in self.get_edges():
            # Only include edges where src or dst is of the requested type
            src_type = self.graph.nodes.get(src, {}).get("type")
            dst_type = self.graph.nodes.get(dst, {}).get("type")
            if src_type == node_type or dst_type == node_type:
                edges.append((src, dst))
        return pagerank_influence(edges, alpha=alpha)

    def get_wallet_pnl_summary(self, wallet: str) -> Dict[str, float]:
        summary = {"wins": 0, "losses": 0, "pnl_total": 0.0}
        for src, dst, attr in self.get_edges(wallet):
            if "pnl" in attr and attr.get("relation", "").startswith("buy"):
                pnl = attr.get("pnl", 0)
                summary["pnl_total"] += pnl
                if pnl > 0:
                    summary["wins"] += 1
                else:
                    summary["losses"] += 1
        return summary


# --- Singleton Access ---
_ENGINE: Optional[MemoryGraph] = None


def graph_store() -> MemoryGraph:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = MemoryGraph()
    return _ENGINE
