"""
Data pipeline for ML models: loads, preprocesses, and formats Solana data for training/inference.
"""
import pandas as pd
import numpy as np

# Example: Load historical price data

def load_price_data(path: str) -> pd.DataFrame:
    return pd.read_csv(path)

# Example: Preprocess for ML

def preprocess_prices(df: pd.DataFrame) -> pd.DataFrame:
    # Fill missing, normalize, etc.
    df = df.fillna(method='ffill')
    df['norm_price'] = (df['price'] - df['price'].mean()) / df['price'].std()
    return df

# Add more loaders for rug, wallet, etc.
