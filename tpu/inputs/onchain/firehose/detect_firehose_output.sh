#!/bin/bash
# 🔍 Firehose Output Detector + Packet Capture
# Captures network and file activity from the firehose binary.

PROCESS_NAME="firehose-solana"
PCAP_FILE="firehose_capture_$(date +%Y%m%d_%H%M%S).pcap"

echo "⏳ Waiting for $PROCESS_NAME to start..."
while ! pgrep -x "$PROCESS_NAME" > /dev/null; do
    sleep 1
done

PID=$(pgrep -x "$PROCESS_NAME")
echo "✅ Detected $PROCESS_NAME (PID $PID)"
echo

# === 1. Show open network connections ===
echo "🌐 Open network connections:"
sudo lsof -Pan -p $PID -i
echo

# === 2. Show open files ===
echo "📂 Files currently open by $PROCESS_NAME:"
lsof -p $PID | grep REG
echo

# === 3. Start packet capture ===
echo "📡 Capturing network traffic to $PCAP_FILE (Press Ctrl+C to stop)..."
# We'll capture only traffic from the firehose process
# First, find the network interface used by the process
INTERFACES=$(sudo lsof -Pan -p $PID -i | awk 'NR>1 {print $9}' | cut -d' ' -f1 | cut -d':' -f1 | sort -u)

if [ -z "$INTERFACES" ]; then
    echo "⚠️ No active interfaces detected for this process. Capturing on 'any'."
    INTERFACES="any"
fi

# Start capture (up to 10MB to avoid disk spam)
sudo tcpdump -i $INTERFACES -w "$PCAP_FILE" -s 0 &
TCPDUMP_PID=$!

# === 4. Live sniff output (human readable) ===
echo "🔍 Live traffic (Press Ctrl+C to stop)..."
sudo tcpdump -i $INTERFACES -A &
LIVE_PID=$!

# Wait for Ctrl+C to stop both
trap "echo; echo '🛑 Stopping captures...'; sudo kill $TCPDUMP_PID $LIVE_PID" INT
wait
