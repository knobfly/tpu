"""
Script to train all ML models: price prediction, rug detection, wallet behavior.
"""
import pandas as pd
from data_pipeline import load_price_data, preprocess_prices
from price_predictor import PricePredictor, train_model as train_price
from rug_detector import RugDetector, train_model as train_rug
from wallet_behavior_model import WalletBehaviorModel, train_model as train_wallet

# Example paths (relative to project root)
import os
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PRICE_DATA_PATH = os.path.join(DATA_DIR, "price_data.csv")
RUG_DATA_PATH = os.path.join(DATA_DIR, "rug_data.csv")
WALLET_DATA_PATH = os.path.join(DATA_DIR, "wallet_data.csv")

# --- Price Prediction ---
try:
    price_df = load_price_data(PRICE_DATA_PATH)
    price_df = preprocess_prices(price_df)
    model = PricePredictor(input_dim=price_df.shape[1])
    train_price(price_df)
    print("Price prediction model trained.")
except Exception as e:
    print(f"Price model error: {e}")

# --- Rug Detection ---
try:
    rug_df = pd.read_csv(RUG_DATA_PATH)
    model = RugDetector(input_dim=rug_df.shape[1])
    train_rug(rug_df)
    print("Rug detection model trained.")
except Exception as e:
    print(f"Rug model error: {e}")

# --- Wallet Behavior ---
try:
    wallet_df = pd.read_csv(WALLET_DATA_PATH)
    model = WalletBehaviorModel(input_dim=wallet_df.shape[1])
    train_wallet(wallet_df)
    print("Wallet behavior model trained.")
except Exception as e:
    print(f"Wallet model error: {e}")
