from inputs.nft.tensor_lister import list_nft_for_sale


async def maybe_list_nft_for_sale(nft: dict, desired_profit_sol: float = 0.1):
    """
    Attempts to list an NFT for sale if conditions are met.
    """
    mint = nft.get("mint")
    buy_price = nft.get("buy_price_sol", 0.0)

    if not mint or buy_price <= 0:
        logging.info(f"[NFT] Invalid listing attempt: {nft}")
        return

    list_price = round(buy_price + desired_profit_sol, 2)

    try:
        success = await list_nft_for_sale(
            mint_address=mint,
            price=list_price,
            owner=nft.get("owner"),
            source="nyx_auto"
        )
        if success:
            logging.info(f"[NFT] ✅ Listed {mint} for {list_price} SOL")
        else:
            logging.warning(f"[NFT] ❌ Failed to list {mint}")
    except Exception as e:
        logging.error(f"[NFT] Error listing NFT: {e}")
