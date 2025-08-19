# /firehose/udp_handshake_client.py

import logging
import socket

FIREHOSE_HOST = "127.0.0.1"
FIREHOSE_PORT = 1337

def send_udp_handshake():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"ready", (FIREHOSE_HOST, FIREHOSE_PORT))
            logging.info("[UDP] 🔌 Handshake sent to Firehose server")
    except Exception as e:
        logging.error(f"[UDP] Handshake failed: {e}")
