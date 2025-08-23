import json

from solders.keypair import Keypair

# ⛔ Replace only the text below, not the quotes
base58_key = "4RjmuUNAraVa6Hhkj6dkV5ua2XJrfwZ6TMg1WKv2wbuXLUnTHtnMrTDb4uVwzhRBob8jX8DDXjnirTrpNnnupvaS"

# Convert and save as wallet.json
keypair = Keypair.from_base58_string(base58_key)
key_bytes = list(bytes(keypair))

with open("/home/ubuntu/nyx/sniper_bot/wallets/observer_x.json", "w") as f:
    json.dump(key_bytes, f)

print(f"✅ Converted and saved wallet2.json ({len(key_bytes)} bytes)")
