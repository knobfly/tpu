import networkx as nx


def pagerank_influence(edges: list[tuple[str,str]], alpha: float = 0.85) -> dict[str,float]:
    """
    edges: [(source, target)] e.g., (wallet -> token) or (group -> token)
    returns {node: score}
    """
    g = nx.DiGraph()
    g.add_edges_from(edges)
    if g.number_of_nodes() == 0:
        return {}
    return nx.pagerank(g, alpha=alpha)
