import logging
from typing import List

from solana.publickey import PublicKey
from solana.transaction import AccountMeta, TransactionInstruction

# Orca Whirlpool Program (default)
ORCA_AMM_PROGRAM = PublicKey("9WwWjW9wxyD4QZq6QMQJwPCFSZBvbGB9n6NFr7nq4ZcT")

def make_swap_instruction(
    payer: PublicKey,
    pool_pubkey: PublicKey,
    token_mint: PublicKey,
    amount_in: int,
    min_amount_out: int = 1,
) -> TransactionInstruction:
    """
    Creates a swap instruction for Orca AMM.
    """
    try:
        ix_data = bytearray([4])  # 4 = swap instruction for Orca
        ix_data += amount_in.to_bytes(8, "little")
        ix_data += min_amount_out.to_bytes(8, "little")

        keys: List[AccountMeta] = [
            AccountMeta(pubkey=payer, is_signer=True, is_writable=True),
            AccountMeta(pubkey=pool_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=token_mint, is_signer=False, is_writable=True),
        ]

        ix = TransactionInstruction(
            keys=keys,
            program_id=ORCA_AMM_PROGRAM,
            data=bytes(ix_data),
        )

        logging.info(f"[OrcaIX] Swap IX created for {token_mint}")
        return ix
    except Exception as e:
        logging.error(f"[OrcaIX] Failed to create swap instruction: {e}")
        return None
