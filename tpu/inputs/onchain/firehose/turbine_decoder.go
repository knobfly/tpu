// turbine_decoder.go
package rpc

import (
	"bytes"
	"fmt"
	"log"

	"google.golang.org/protobuf/proto"

	"nyx/inputs/onchain/firehose/firehose_v2"
)

// decodeTurbinePacket attempts to decode raw UDP bytes into a Firehose Block proto
func decodeTurbinePacket(data []byte) (*firehose_v2.Block, error) {
	if len(data) == 0 {
		return nil, fmt.Errorf("empty packet")
	}

	block := &firehose_v2.Block{}
	err := proto.Unmarshal(data, block)
	if err != nil {
		return nil, fmt.Errorf("proto decode failed: %w", err)
	}

	return block, nil
}

// handleDecodedBlock logs and broadcasts a decoded block
func handleDecodedBlock(block *firehose_v2.Block) {
	if block == nil {
		return
	}
	log.Printf("🧩 Decoded Turbine Block — Slot: %d Hash: %s Parent: %s",
		block.BlockNum, block.BlockHash, block.Parent)

	// Send JSON version to WebSocket clients
	broadcastWS(map[string]interface{}{
		"slot":   block.BlockNum,
		"hash":   block.BlockHash,
		"parent": block.Parent,
	})
}

// tryDecodeAndBroadcast is called by turbine.go when new packets arrive
func tryDecodeAndBroadcast(data []byte) {
	block, err := decodeTurbinePacket(data)
	if err != nil {
		log.Printf("⚠️ Turbine decode error: %v", err)
		return
	}
	handleDecodedBlock(block)
}
