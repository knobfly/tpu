import asyncio
import logging
import os
import time
from datetime import datetime

# Bitquery fallback
from chart.bitquery_analytics import fetch_token_ohlcv
from inputs.onchain.firehose.packet_listener import get_recent_ohlcv, start_packet_listener
from inputs.onchain.firehose.proto_decoder import decode_firehose_packet
from utils.logger import log_event
from utils.service_status import update_status

TEST_DURATION = 10  # seconds
TRACE_LOG = "/home/ubuntu/nyx/runtime/logs/firehose_trace.log"

# === Configure Logging ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/home/ubuntu/nyx/runtime/logs/firehose_test_runner.log"),
        logging.StreamHandler()
    ]
)

async def test_proto_decoder():
    try:
        sample_packet = b"\x0a\x07testing"  # A mock byte stream
        decoded = decode_firehose_packet(sample_packet)
        log_event(f"[FirehoseTest] ProtoDecoder OK → {decoded}")
        return True
    except Exception as e:
        logging.error(f"[FirehoseTest] ProtoDecoder failed: {e}")
        return False

async def test_firehose_stream():
    log_event("[FirehoseTest] Starting Firehose packet listener test...")
    start_time = time.time()
    packet_count = 0
    failed = False

    try:
        task = asyncio.create_task(start_packet_listener(run_forever=False))
        while time.time() - start_time < TEST_DURATION:
            await asyncio.sleep(1)
            if os.path.exists(TRACE_LOG):
                with open(TRACE_LOG, "r") as f:
                    lines = f.readlines()
                    packet_count = len(lines)
        task.cancel()
        log_event(f"[FirehoseTest] Packets received: {packet_count} in {TEST_DURATION}s")
        return packet_count > 0
    except Exception as e:
        failed = True
        logging.error(f"[FirehoseTest] Stream test failed: {e}")
    return not failed

async def test_ohlcv(token_address: str):
    try:
        candles = get_recent_ohlcv(token_address, minutes=30)
        if candles:
            log_event(f"[FirehoseTest] OHLCV from Firehose: {len(candles)} candles")
            return candles
        else:
            log_event("[FirehoseTest] No OHLCV from Firehose, trying Bitquery fallback...")
            candles = await fetch_token_ohlcv(token_address, interval="1m", lookback=30)
            log_event(f"[FirehoseTest] OHLCV from Bitquery: {len(candles)} candles")
            return candles
    except Exception as e:
        logging.error(f"[FirehoseTest] OHLCV fetch failed: {e}")
        return []

async def run_full_test(token_address: str):
    update_status("firehose_test_runner")
    logging.info("=== Starting Firehose Test Runner ===")

    proto_ok = await test_proto_decoder()
    stream_ok = await test_firehose_stream()
    candles = await test_ohlcv(token_address)

    logging.info("\n=== Firehose Test Summary ===")
    logging.info(f"ProtoDecoder: {'✅ OK' if proto_ok else '❌ FAILED'}")
    logging.info(f"Firehose Stream: {'✅ OK' if stream_ok else '❌ FAILED'}")
    logging.info(f"OHLCV Candles: {len(candles)}")
    logging.info("=============================")

    return {
        "proto": proto_ok,
        "stream": stream_ok,
        "ohlcv_count": len(candles)
    }

if __name__ == "__main__":
    test_token = os.environ.get("TEST_TOKEN", "So11111111111111111111111111111111111111112")
    asyncio.run(run_full_test(test_token))
