from inputs.wallet.wallet_core import WalletManager
from memory.token_confidence_engine import update_token_confidence
from utils.logger import log_event

alpha_wallets = set()
wallet_token_map = {}

def mark_alpha_wallet(address: str):
    alpha_wallets.add(address)
    log_event(f"🧠 Alpha wallet marked: {address}")

def link_wallet_to_token(wallet: str, mint: str):
    if wallet not in wallet_token_map:
        wallet_token_map[wallet] = set()
    wallet_token_map[wallet].add(mint)

def boost_token_from_wallet(wallet: str, mint: str):
    if wallet in alpha_wallets:
        update_token_confidence(mint, delta=0.1, source="wallet_signal")
        log_event(f"[WalletSignal] Boosted {mint} via alpha wallet {wallet}")

def run_wallet_link_analysis():
    for wallet, mints in wallet_token_map.items():
        if wallet in alpha_wallets:
            for mint in mints:
                update_token_confidence(mint, delta=0.05, source="wallet_signal")

def reset_wallet_signal_map():
    wallet_token_map.clear()
