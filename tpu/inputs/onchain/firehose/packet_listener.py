# /firehose/packet_listener.py

import asyncio
import logging
import time
from collections import deque
from typing import Deque

import websockets
from inputs.onchain.firehose.firehose_health_monitor import firehose_health_monitor
from inputs.onchain.firehose.firehose_watchdog import update_packet_heartbeat
from inputs.onchain.firehose.metrics import firehose_metrics
from inputs.onchain.firehose.ohlcv_builder import get_ohlcv_window, push_trade
from inputs.onchain.firehose.proto_decoder import decode_firehose_packet
from inputs.onchain.firehose.stream_router import route_event

# === Config & State ===
FIREHOSE_HOST = "127.0.0.1"
FIREHOSE_PORT = 1337
STALL_TIMEOUT = 5  # seconds without packets before reconnect

_RECENT_TRADES: Deque[dict] = deque(maxlen=10_000)
_LAST_ONCHAIN_TS = 0.0
_LIVE = False


async def listen_to_firehose():
    """
    WebSocket listener for firehose packets.
    Decodes and routes packets, updates OHLCV and metrics.
    Retries on connection failure or stalls.
    """
    global _LAST_ONCHAIN_TS, _LIVE
    uri = f"ws://{FIREHOSE_HOST}:{FIREHOSE_PORT}"

    while True:
        try:
            logging.info(f"[Firehose] 🔌 Connecting to {uri}...")
            async with websockets.connect(uri, ping_interval=None, max_size=None) as ws:
                logging.info(f"[Firehose] ✅ Connected to {uri}")
                _LIVE = True
                firehose_metrics.reset()

                last_packet_time = time.time()
                first_packet_logged = False

                async for raw_data in ws:
                    # Convert to bytes if needed
                    if isinstance(raw_data, str):
                        raw_data = raw_data.encode("utf-8")

                    # Debug first packet
                    if not first_packet_logged:
                        logging.info(f"[Firehose] First packet received ({len(raw_data)} bytes)")
                        first_packet_logged = True

                    try:
                        start_decode = time.time()
                        decoded = decode_firehose_packet(raw_data)
                        decode_latency_ms = (time.time() - start_decode) * 1000

                        if decoded and isinstance(decoded, dict):
                            last_packet_time = time.time()
                            _RECENT_TRADES.append(decoded)
                            push_trade(decoded)
                            await route_event(decoded)  # Pass only dicts
                            update_packet_heartbeat()
                            firehose_metrics.on_trade(decoded)
                            firehose_health_monitor.record_packet(decode_latency_ms=decode_latency_ms)

                            parsed_block_ts = decoded.get("ts", 0.0)
                            if parsed_block_ts:
                                _LAST_ONCHAIN_TS = max(_LAST_ONCHAIN_TS, parsed_block_ts)
                        else:
                            logging.warning(f"[Firehose] Skipped packet — invalid decode result: {type(decoded)}")

                        # Stall detection
                        if time.time() - last_packet_time > STALL_TIMEOUT:
                            logging.warning(f"[Firehose] ⚠️ No packets for {STALL_TIMEOUT}s, reconnecting...")
                            break  # exits async for loop, triggers reconnect

                    except Exception as inner_e:
                        logging.warning(f"[Firehose] Packet error: {inner_e}")

        except (OSError, websockets.exceptions.ConnectionClosedError, ConnectionRefusedError) as conn_e:
            logging.warning(f"[Firehose] Connection error: {conn_e}. Retrying in 2s...")
            _LIVE = False
            await asyncio.sleep(2)
        except Exception as e:
            logging.error(f"[Firehose] Unexpected error: {e}. Retrying in 5s...")
            _LIVE = False
            await asyncio.sleep(5)


async def start_packet_listener():
    await listen_to_firehose()


def get_recent_trades(limit: int = 1000):
    return list(_RECENT_TRADES)[-limit:]


def get_recent_ohlcv(token: str, window_seconds: int = 1800, granularity_s: int = 60):
    return get_ohlcv_window(token, window_seconds, granularity_s)


def get_current_tps():
    return firehose_metrics.tps


def is_live() -> bool:
    return _LIVE


def get_last_onchain_ts() -> float:
    return _LAST_ONCHAIN_TS
