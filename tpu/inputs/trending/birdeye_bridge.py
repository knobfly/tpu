# modules/birdeye_bridge.py

import logging

import requests
from core.live_config import config

BIRDEYE_API_KEY = config.get("birdeye_api_key", "")
BASE_URL = "https://public-api.birdeye.so/"

def get_token_info(token_address: str):
    try:
        url = f"{BASE_URL}token/{token_address}?api_key={BIRDEYE_API_KEY}"
        response = requests.get(url)
        if response.status_code == 200:
            return response.json().get("data", {})
        else:
            logging.warning(f"[BirdEye] Token info failed: {response.status_code}")
            return {}
    except Exception as e:
        logging.warning(f"[BirdEye] Error: {e}")
        return {}
