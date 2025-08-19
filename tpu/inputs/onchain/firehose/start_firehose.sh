#!/bin/bash
cd "$(dirname "$0")"

MAX_RETRIES=3
ATTEMPT=0

# Local validator RPC
LOCAL_RPC="http://127.0.0.1:8899"

# State directory
STATE_DIR="./poller_state"

# Offset slots from head to avoid incomplete blocks
SLOT_OFFSET=5000

# Function to check if local RPC is alive
function rpc_alive() {
  curl -s "$1" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"getSlot"}' | jq -r '.result' | grep -q '^[0-9]\+$'
}

# Wait for local RPC to be ready
echo "⏳ Waiting for local validator RPC at $LOCAL_RPC..."
until rpc_alive "$LOCAL_RPC"; do
  echo "   Local RPC not ready yet... retrying in 5s"
  sleep 5
done
echo "✅ Local RPC detected — switching Firehose to use it."

# Get latest slot from local RPC
SLOT=$(curl -s "$LOCAL_RPC" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"getSlot"}' | jq -r '.result')

START_SLOT=$((SLOT - SLOT_OFFSET))
echo "📦 Starting Firehose from slot $START_SLOT (latest: $SLOT)"

rm -rf "$STATE_DIR"
mkdir -p "$STATE_DIR"

while [ $ATTEMPT -lt $MAX_RETRIES ]; do
  echo "🔥 Attempt $(($ATTEMPT + 1)) of $MAX_RETRIES..."

  ./firehose-solana fetch rpc $START_SLOT \
    --network mainnet \
    --endpoints "$LOCAL_RPC" \
    --state-dir="$STATE_DIR" \
    --interval-between-fetch 0 \
    --latest-block-retry-interval 1s \
    --max-block-fetch-duration 3s \
    --interval-between-clients-sort 10m \
    --block-fetch-batch-size 10 \
    --write-to-file=false \
    --websocket-out 127.0.0.1:1337 \
    --heartbeat-interval 30s

  STATUS=$?
  if [ $STATUS -eq 0 ]; then
    echo "✅ Firehose exited cleanly."
    exit 0
  else
    echo "⚠️ Firehose crashed with code $STATUS. Retrying..."
  fi

  ATTEMPT=$((ATTEMPT + 1))
  sleep 3
done

echo "❌ Firehose failed after $MAX_RETRIES attempts."
exit 1

