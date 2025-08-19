# /firehose/proto_decoder.py

import logging

from inputs.onchain.firehose.sf__firehose__v2__block_pb2 import Response


def decode_firehose_packet(data: bytes) -> dict:
    try:
        response = Response()
        response.ParseFromString(data)

        if not response.block:
            return None

        block = response.block
        transactions = []

        for tx in block.transactions:
            transactions.append({
                "tx_hash": tx.meta.tx_hash.hex(),
                "instructions": [ix.program_id_index for ix in tx.instructions],
                "index": tx.index,
                "logs": list(tx.meta.log_messages),
            })

        return {
            "slot": block.slot,
            "transactions": transactions,
        }

    except Exception as e:
        logging.error(f"[Decoder] Failed to decode protobuf: {e}")
        return None
