import logging
from typing import List

from solana.publickey import PublicKey
from solana.transaction import AccountMeta, TransactionInstruction

# Raydium AMM Program IDs
RAYDIUM_AMM_V4_PROGRAM = PublicKey("675kPX9MHTjS2zt1qfr1NYHqWgJz7jzH6tK5g84n4hS")
RAYDIUM_SERUM_PROGRAM = PublicKey("9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin")

def make_swap_instruction(
    payer: PublicKey,
    pool_pubkey: PublicKey,
    token_mint: PublicKey,
    amount_in: int,
    min_amount_out: int = 1,
) -> TransactionInstruction:
    """
    Creates a swap instruction for Raydium AMM.
    """
    try:
        # Minimal Raydium IX data structure
        # Format: [instruction_id (1 byte), amount_in (8 bytes), min_amount_out (8 bytes)]
        ix_data = bytearray([9])  # 9 = swap instruction
        ix_data += amount_in.to_bytes(8, "little")
        ix_data += min_amount_out.to_bytes(8, "little")

        keys: List[AccountMeta] = [
            AccountMeta(pubkey=payer, is_signer=True, is_writable=True),
            AccountMeta(pubkey=pool_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=token_mint, is_signer=False, is_writable=True),
            AccountMeta(pubkey=RAYDIUM_SERUM_PROGRAM, is_signer=False, is_writable=False),
        ]

        ix = TransactionInstruction(
            keys=keys,
            program_id=RAYDIUM_AMM_V4_PROGRAM,
            data=bytes(ix_data),
        )

        logging.info(f"[RaydiumIX] Swap IX created for {token_mint}")
        return ix
    except Exception as e:
        logging.error(f"[RaydiumIX] Failed to create swap instruction: {e}")
        return None
