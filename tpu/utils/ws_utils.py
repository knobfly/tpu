# modules/utils/ws_utils.py

import asyncio
import logging

import websockets


async def resilient_websocket_loop(uri, on_message):
    """
    Connects to a websocket URI and calls `on_message(msg)` for each message.
    Automatically reconnects on failure.
    """
    while True:
        try:
            async with websockets.connect(uri) as ws:
                logging.info(f"[WebSocket] Connected: {uri}")
                async for message in ws:
                    await on_message(message)
        except websockets.ConnectionClosed as e:
            logging.warning(f"[WebSocket] Connection closed: {e}. Reconnecting...")
        except Exception as e:
            logging.error(f"[WebSocket] Unexpected error: {e}")
        await asyncio.sleep(5)
