def profile_trade_context(signals: dict, reasoning: list[str]) -> list[str]:
    """
    Returns a list of high-level meta tags based on signals and reasoning.

    Example:
        signals = {
            "whales": True,
            "bundle": True,
            "celeb_detected": True,
            ...
        }

        reasoning = ["whale", "chart", "copy_launch"]

        Output: ["celeb", "whale", "bundle", "copy"]
    """
    tags = set()

    # === Theme keywords from reasoning
    for r in reasoning:
        r = r.lower()
        if "celeb" in r:
            tags.add("celeb")
        if "whale" in r:
            tags.add("whale")
        if "bundle" in r:
            tags.add("bundle")
        if "copy" in r or "clone" in r:
            tags.add("copy")
        if "relaunch" in r:
            tags.add("relaunch")

    # === Signal-based tags
    if signals.get("whales"):
        tags.add("whale")
    if signals.get("bundle"):
        tags.add("bundle")
    if signals.get("celeb_detected"):
        tags.add("celeb")
    if signals.get("copy_launch"):
        tags.add("copy")
    if signals.get("relaunch"):
        tags.add("relaunch")

    return list(tags)
