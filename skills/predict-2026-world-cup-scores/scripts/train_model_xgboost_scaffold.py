#!/usr/bin/env python3
"""
Scaffold for Engine A: XGBoost / LightGBM Machine Learning Model
To be used as part of the Triple-Engine Ensemble architecture.

Requirements:
    pip install xgboost scikit-learn pandas
"""

import sqlite3
import json
from pathlib import Path

# Try importing ML libraries. If missing, print a helpful message.
try:
    import pandas as pd
    import xgboost as xgb
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import brier_score_loss, log_loss
except ImportError:
    print("Machine learning libraries not found. Please run: pip install xgboost scikit-learn pandas")
    exit(1)

def load_training_data(db_path: Path):
    """Load historical matches from training_matches table and join with team features."""
    conn = sqlite3.connect(db_path)
    
    # Query fetches historical matches and their respective xG or scores
    query = """
        SELECT 
            tm.team_a_id, tm.team_b_id, 
            tm.score_a, tm.score_b,
            tm.xg_a, tm.xg_b,
            tm.neutral_site,
            tm.is_knockout
        FROM training_matches tm
        WHERE tm.score_a IS NOT NULL AND tm.score_b IS NOT NULL
    """
    df = pd.read_sql_query(query, conn)
    
    # In a full implementation, you would join this with team_strength_snapshots 
    # and team_style_profiles ON the match date.
    
    # Create target classes: 0 = Away Win, 1 = Draw, 2 = Home Win
    def get_class(row):
        if row['score_a'] > row['score_b']: return 2
        elif row['score_a'] == row['score_b']: return 1
        else: return 0
        
    df['target'] = df.apply(get_class, axis=1)
    return df

def train_xgboost(df):
    """Train the XGBoost classifier on historical match features."""
    # Feature engineering (Placeholder for actual features like attack_edge, press_edge)
    features = ['neutral_site', 'is_knockout'] 
    
    X = df[features]
    y = df['target']
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # XGBoost model tuned for multiclass probability prediction
    model = xgb.XGBClassifier(
        objective='multi:softprob',
        num_class=3,
        learning_rate=0.05,
        max_depth=4,
        eval_metric='mlogloss'
    )
    
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=10)
    
    # Evaluate
    preds = model.predict_proba(X_test)
    loss = log_loss(y_test, preds)
    print(f"Validation Log Loss: {loss:.4f}")
    
    return model

if __name__ == "__main__":
    db_path = Path("data/worldcup2026.sqlite")
    if not db_path.exists():
        print(f"Database not found at {db_path}")
        exit(1)
        
    print("Loading training data...")
    df = load_training_data(db_path)
    print(f"Loaded {len(df)} training matches.")
    
    print("Training Engine A (XGBoost)...")
    model = train_xgboost(df)
    
    print("Model trained successfully. You can now save it and integrate it into predict_match.py as Engine A.")
