import asyncio
import os
import time
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple

from core.live_config import config
from inputs.wallet.wallet_core import LAMPORTS_PER_SOL, RECEIVER_WALLET, WalletManager
from solana.publickey import PublicKey
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solana.system_program import TransferParams, transfer
from solana.transaction import Transaction
from utils.logger import log_event
from utils.rpc_loader import get_active_rpc

WALLETS_DIR = "/home/ubuntu/nyx/wallets"  # absolute
DEFAULT_SKIM_PCT = 0.25
DEFAULT_MIN_TOPUP = 0.50
DEFAULT_MIN_THRESHOLD = 0.10


@dataclass
class WalletStats:
    realized_pnl_sol: float = 0.0
    skimmed_sol: float = 0.0
    trades: int = 0
    last_trade_ts: float = 0.0

    def to_dict(self):
        return asdict(self)


class MultiWalletManager:
    """
    - Loads all json keypairs in WALLETS_DIR
    - Derives a *role* from filename (alpha_rotator.json -> role 'alpha_rotator')
    - Lets you:
        * choose_wallet(role=...)  -> WalletManager
        * record_trade_pnl(...)    -> logs pnl + skims % of profit to RECEIVER_WALLET
        * get_all_balances/report  -> telegram-friendly dump
    """

    def __init__(self):
        self.wallets: List[WalletManager] = []
        self.role_index: Dict[str, WalletManager] = {}
        self.stats: Dict[str, WalletStats] = {}     # by wallet.address
        self._main_wallet: Optional[WalletManager] = None  # optional, if you ever want a real keypair vault
        self._skim_pct: float = float(config.get("profit_skim_pct", DEFAULT_SKIM_PCT))
        self._min_topup: float = float(config.get("wallet_min_topup", DEFAULT_MIN_TOPUP))

    # -------------- Load / Index --------------

    def load_all_wallets(self):
        self.wallets.clear()
        self.role_index.clear()

        for filename in os.listdir(WALLETS_DIR):
            if not filename.endswith(".json"):
                continue
            path = os.path.join(WALLETS_DIR, filename)
            role = filename.replace(".json", "")
            wallet = WalletManager.from_file(path, role)
            if wallet:
                self.wallets.append(wallet)
                self.role_index[role] = wallet
                self.stats.setdefault(wallet.address, WalletStats())
            else:
                log_event(f"⚠️ Failed to load wallet {filename}")

        log_event(f"🔢 Loaded {len(self.wallets)} wallet(s) from {WALLETS_DIR}")

    def set_main_wallet(self, wallet: WalletManager):
        """ Optional: if you want to ALSO top-up trading wallets from a real vault keypair you control. """
        self._main_wallet = wallet

    # -------------- Selection --------------

    async def get_best_wallet(self, min_threshold: float = DEFAULT_MIN_THRESHOLD,
                              rpc_url: Optional[str] = None) -> Optional[WalletManager]:
        rpc = rpc_url or get_active_rpc()
        best_wallet = None
        best_balance = 0.0
        for wallet in self.wallets:
            try:
                balance = await wallet.get_balance(rpc)
                if balance > min_threshold and balance > best_balance:
                    best_wallet = wallet
                    best_balance = balance
            except Exception as e:
                log_event(f"⚠️ Failed to check balance for {wallet.address}: {e}")
        return best_wallet

    def choose_wallet(self, role: Optional[str] = None,
                      fallback_best: bool = True) -> Optional[WalletManager]:
        """
        Fast, synchronous selector (no balance check). Use when you already planned
        by role. If no role or not found, optionally fallback to best wallet
        (you can then check balance async before trading).
        """
        if role and role in self.role_index:
            return self.role_index[role]
        if not fallback_best:
            return None
        # NOTE: this is sync; if you truly want best-by-balance, call get_best_wallet() async.
        return self.wallets[0] if self.wallets else None

    def get_wallet_by_address(self, address: str) -> Optional[WalletManager]:
        for wallet in self.wallets:
            if wallet.address == address:
                return wallet
        return None

    def get_wallet_by_index(self, index: int) -> Optional[WalletManager]:
        if 0 <= index < len(self.wallets):
            return self.wallets[index]
        return None

    def get_main_wallet(self) -> Optional[WalletManager]:
        # By convention: first loaded, or explicitly set_main_wallet()
        return self._main_wallet or (self.wallets[0] if self.wallets else None)

    # -------------- Balances & Reports --------------
    async def get_wallets_report(self) -> str:
        balances = await self.get_all_balances()
        lines = ["💼 *Wallets Report*"]
        if not balances:
            lines.append("❌ No wallet balances returned. Check RPC or wallet loading.")
        for name, addr, bal in balances:
            s = self.stats.get(addr)
            pnl = f"{s.realized_pnl_sol:.3f} SOL" if s else "0.000 SOL"
            skim = f"{s.skimmed_sol:.3f} SOL" if s else "0.000 SOL"
            trades = s.trades if s else 0
            lines.append(f"• `{name}` `{addr[:6]}...{addr[-4:]}` — {bal:.4f} SOL | 📊 PnL: {pnl} | 💸 Skim: {skim} | 🔁 Trades: {trades}")
        return "\n".join(lines)


    async def get_all_balances(self, rpc_url: Optional[str] = None) -> List[Tuple[str, str, float]]:
        rpc = rpc_url or get_active_rpc()
        log_event(f"🔎 [get_all_balances] Using RPC: {rpc}")
        results = []
        try:
            async with AsyncClient(rpc) as client:
                for wallet in self.wallets:
                    log_event(f"🌐 Checking balance for {wallet.name} / {wallet.address}")
                    try:
                        res = await client.get_balance(wallet.public_key)
                        lamports = res["result"]["value"] if isinstance(res, dict) else 0
                        sol = lamports / 1e9
                        results.append((wallet.name, wallet.address, round(sol, 4)))
                    except Exception as e:
                        log_event(f"❌ Error fetching balance for {wallet.address}: {e}")
        except Exception as e:
            log_event(f"❌ get_all_balances RPC session error: {e}")
        return results

    # -------------- Profit Skim / PnL Tracking --------------

    async def record_trade_pnl(self,
                               wallet: WalletManager,
                               pnl_sol: float,
                               was_profit: bool,
                               tx_sig: str,
                               token: str,
                               rpc_url: Optional[str] = None):
        """
        Call this after you close a position (or compute realized PnL).
        If profit, skim % to RECEIVER_WALLET.
        """
        addr = wallet.address
        stat = self.stats.setdefault(addr, WalletStats())
        stat.trades += 1
        stat.last_trade_ts = time.time()

        if was_profit and pnl_sol > 0:
            stat.realized_pnl_sol += pnl_sol

            skim_pct = self._skim_pct
            skim_amount = pnl_sol * skim_pct
            if skim_amount > 0:
                ok = await self._skim_to_vault(wallet, skim_amount, rpc_url=rpc_url)
                if ok:
                    stat.skimmed_sol += skim_amount
                    log_event(f"💸 Skimmed {skim_amount:.4f} SOL from {wallet.name} → profit vault (TX: {tx_sig})")
                else:
                    log_event(f"⚠️ Failed to skim {skim_amount:.4f} SOL to vault from {wallet.name}")

        else:
            # loss or 0 pnl
            stat.realized_pnl_sol += pnl_sol  # negative or 0
            log_event(f"📉 Loss/Flat recorded for {wallet.name}: {pnl_sol:.4f} SOL (TX: {tx_sig})")

    async def _skim_to_vault(self,
                             from_wallet: WalletManager,
                             amount_sol: float,
                             rpc_url: Optional[str] = None) -> bool:
        """
        Transfer amount_sol from the given wallet to RECEIVER_WALLET.
        """
        try:
            rpc = rpc_url or get_active_rpc()
            async with AsyncClient(rpc) as client:
                blockhash = (await client.get_latest_blockhash())["result"]["value"]["blockhash"]

                tx = Transaction(recent_blockhash=blockhash)
                tx.add(
                    transfer(
                        TransferParams(
                            from_pubkey=from_wallet.public_key,
                            to_pubkey=RECEIVER_WALLET,
                            lamports=int(amount_sol * LAMPORTS_PER_SOL),
                        )
                    )
                )
                tx.sign(from_wallet.keypair)
                sig = await client.send_transaction(tx, from_wallet.keypair, opts=TxOpts(skip_preflight=True))
                if isinstance(sig, dict):
                    sig = sig.get("result")
                log_event(f"✅ Skim TX sent: {sig}")
                return True
        except Exception as e:
            log_event(f"❌ _skim_to_vault failed: {e}")
        return False

    # -------------- Optional: top-up trading wallets --------------

    async def maybe_topup_wallet(self,
                                 target_wallet: WalletManager,
                                 min_balance_sol: Optional[float] = None,
                                 rpc_url: Optional[str] = None) -> bool:
        """
        If you want to make sure a wallet never goes below X SOL and you control a vault keypair.
        NOTE: This requires you to have a *signing* main/vault wallet. If you only
        send to RECEIVER_WALLET (cold), skip this.
        """
        if self._main_wallet is None:
            return False

        threshold = min_balance_sol or self._min_topup
        rpc = rpc_url or get_active_rpc()
        cur = await target_wallet.get_balance(rpc)
        if cur >= threshold:
            return False

        diff = threshold - cur
        try:
            async with AsyncClient(rpc) as client:
                blockhash = (await client.get_latest_blockhash())["result"]["value"]["blockhash"]
                tx = Transaction(recent_blockhash=blockhash)
                tx.add(
                    transfer(
                        TransferParams(
                            from_pubkey=self._main_wallet.public_key,
                            to_pubkey=target_wallet.public_key,
                            lamports=int(diff * LAMPORTS_PER_SOL),
                        )
                    )
                )
                tx.sign(self._main_wallet.keypair)
                sig = await client.send_transaction(tx, self._main_wallet.keypair, opts=TxOpts(skip_preflight=True))
                if isinstance(sig, dict):
                    sig = sig.get("result")
                log_event(f"🔁 Topped up {target_wallet.name} with {diff:.4f} SOL (TX: {sig})")
                return True
        except Exception as e:
            log_event(f"❌ Top-up failed: {e}")
            return False

        return False


# === Singleton ===
multi_wallet = MultiWalletManager()
multi_wallet.load_all_wallets()
