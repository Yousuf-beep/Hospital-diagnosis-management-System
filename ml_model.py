"""
ml_model.py — Ensemble ML model (Random Forest + Naive Bayes + SVM)
All symptom strings are stripped of whitespace at load time so they
match the chatbot engine and form submissions consistently.

The trained model is cached in models/ and only retrained when the
dataset or the scikit-learn version changes, so startup stays fast.
"""

import os
import hashlib
import logging

import joblib
import pandas as pd
import sklearn
from sklearn.preprocessing import MultiLabelBinarizer
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.naive_bayes import BernoulliNB
from sklearn.svm import SVC

import config

log = logging.getLogger("mediai.model")
DATASET_PATH = os.path.join(config.BASE_DIR, "dataset", "dataset.csv")


def _fingerprint():
    with open(DATASET_PATH, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()[:16]
    return f"{digest}-sklearn{sklearn.__version__}"


def _train():
    data = pd.read_csv(DATASET_PATH)
    symptom_cols = data.columns[1:]

    # Strip whitespace from every symptom value — the raw CSV has leading spaces
    symptoms_list = [[s.strip() for s in row[symptom_cols].dropna()]
                     for _, row in data.iterrows()]

    mlb = MultiLabelBinarizer()
    X   = mlb.fit_transform(symptoms_list)
    y   = data["Disease"].str.strip()

    # Ensemble: RF + Naive Bayes + SVM (soft voting)
    model = VotingClassifier(
        estimators=[
            ("rf",  RandomForestClassifier(n_estimators=100, random_state=42)),
            ("nb",  BernoulliNB()),
            ("svm", SVC(kernel="linear", probability=True, random_state=42)),
        ],
        voting="soft",
    )
    model.fit(X, y)
    return mlb, model


def _load_or_train():
    path = os.path.join(config.MODEL_DIR, f"ensemble-{_fingerprint()}.joblib")
    if os.path.exists(path):
        try:
            mlb, model = joblib.load(path)
            log.info("Loaded cached model %s", os.path.basename(path))
            return mlb, model
        except Exception:
            log.warning("Cached model unreadable - retraining", exc_info=True)
    log.info("Training ensemble model (first run may take a minute)")
    mlb, model = _train()
    try:
        os.makedirs(config.MODEL_DIR, exist_ok=True)
        for old in os.listdir(config.MODEL_DIR):
            if old.startswith("ensemble-") and old.endswith(".joblib"):
                os.remove(os.path.join(config.MODEL_DIR, old))
        joblib.dump((mlb, model), path)
    except OSError:
        log.warning("Could not cache model to %s", config.MODEL_DIR, exc_info=True)
    return mlb, model


mlb, model = _load_or_train()

# symptom_columns is used by the UI checkbox grid — clean, no leading spaces
symptom_columns = mlb.classes_


def predict_disease(symptoms):
    """Predict top 5 diseases for a list of symptom keys (stripped)."""
    known = [s.strip() for s in symptoms if s.strip() in mlb.classes_]
    vec   = mlb.transform([known])
    probs = model.predict_proba(vec)[0]
    results = sorted(zip(model.classes_, probs), key=lambda x: x[1], reverse=True)
    return [(str(d), float(p)) for d, p in results[:5]]


def get_confidence(predictions):
    """Return top prediction confidence as a percentage float."""
    if not predictions:
        return 0
    return round(float(predictions[0][1]) * 100, 1)
