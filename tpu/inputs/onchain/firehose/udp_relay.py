import json
import os
import socket
import time

BLOCK_DIR = "./poller_state"
UDP_HOST = "127.0.0.1"
UDP_PORT = 1337

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def send_blocks():
    sent = 0
    for filename in sorted(os.listdir(BLOCK_DIR)):
        if filename.endswith(".json") or "block" in filename:
            with open(os.path.join(BLOCK_DIR, filename), "rb") as f:
                data = f.read()
                sock.sendto(data, (UDP_HOST, UDP_PORT))
                sent += 1
                time.sleep(0.05)
    print(f"✅ Sent {sent} blocks")

if __name__ == "__main__":
    send_blocks()
