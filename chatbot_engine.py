"""
chatbot_engine.py — Smart chatbot with severity questions + free-text parsing
"""

import os
import pandas as pd

DATASET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")

data = pd.read_csv(os.path.join(DATASET_DIR, "dataset.csv"))
symptom_cols = data.columns[1:]

# Build disease→symptoms map with ALL whitespace stripped
disease_symptoms = {}
for _, row in data.iterrows():
    disease = row["Disease"].strip()
    disease_symptoms[disease] = set(s.strip() for s in row[symptom_cols].dropna())

# Symptom aliases for free-text parsing
ALIASES = {
    "temp": "high_fever", "temperature": "high_fever", "hot": "high_fever",
    "tired": "fatigue", "exhausted": "fatigue", "weak": "weakness",
    "head": "headache", "migraine": "headache",
    "stomach": "stomach_pain", "belly": "stomach_pain", "tummy": "stomach_pain",
    "throat": "throat_irritation", "sore throat": "throat_irritation",
    "runny nose": "runny_nose", "blocked nose": "congestion",
    "chest": "chest_pain", "heart": "chest_pain",
    "back": "back_pain", "spine": "back_pain",
    "skin": "skin_rash", "rash": "skin_rash", "itch": "itching",
    "breathe": "breathlessness", "breathing": "breathlessness", "short of breath": "breathlessness",
    "throw up": "vomiting", "threw up": "vomiting", "puke": "vomiting",
    "sick": "nausea", "queasy": "nausea",
    "sweat": "sweating", "sweating": "sweating",
    "dizzy": "dizziness", "spinning": "dizziness",
    "joint": "joint_pain", "joints": "joint_pain",
    "muscle": "muscle_pain", "muscles": "muscle_pain",
    "eye": "yellowing_of_eyes", "yellow eye": "yellowing_of_eyes",
    "urine": "dark_urine", "pee": "dark_urine",
    "appetite": "loss_of_appetite", "not eating": "loss_of_appetite",
    "weight": "weight_loss",
    "chill": "chills", "shiver": "chills",
}

# Follow-up severity questions per symptom
SEVERITY_QUESTIONS = {
    "fever":          "How high is your fever?",
    "high_fever":     "How high is your fever?",
    "headache":       "How severe is your headache?",
    "chest_pain":     "How severe is your chest pain?",
    "fatigue":        "How long have you been feeling fatigued?",
    "cough":          "Is your cough dry or with phlegm?",
    "breathlessness": "Does breathlessness occur at rest or only during activity?",
}

SEVERITY_OPTIONS = {
    "How high is your fever?":                   ["Below 100°F (mild)", "100–102°F (moderate)", "Above 102°F (high)"],
    "How severe is your headache?":              ["Mild / dull ache", "Moderate", "Severe / throbbing"],
    "How severe is your chest pain?":            ["Mild discomfort", "Moderate pressure", "Severe / crushing"],
    "How long have you been feeling fatigued?":  ["1–2 days", "3–7 days", "More than a week"],
    "Is your cough dry or with phlegm?":         ["Dry cough", "Wet / with phlegm", "With blood"],
    "Does breathlessness occur at rest or only during activity?": ["Only during activity", "At rest too", "Constant"],
}


def parse_free_text(text):
    """Extract symptom from free-text user input. Always returns a stripped key."""
    text_lower = text.lower().strip()
    # Direct match against known stripped symptoms
    all_symptoms = set(s for syms in disease_symptoms.values() for s in syms)
    for sym in all_symptoms:
        if sym.replace("_", " ") in text_lower:
            return sym
    # Alias match
    for alias, symptom in ALIASES.items():
        if alias in text_lower:
            return symptom
    # Fallback: clean the input
    return text_lower.replace(" ", "_")


def next_question(asked, positive, negative):
    """
    Find the best next symptom to ask about.
    asked/positive/negative must all contain stripped symptom keys.
    Returns (symptom_key, possible_diseases_list).
    """
    # Normalise inputs — strip just in case
    asked    = [s.strip() for s in asked]
    positive = [s.strip() for s in positive]
    negative = [s.strip() for s in negative]

    possible = []
    for disease, symptoms in disease_symptoms.items():
        if (all(p in symptoms for p in positive) and
                not any(n in symptoms for n in negative)):
            possible.append(disease)

    if len(possible) <= 1:
        return None, possible

    freq = {}
    for disease in possible:
        for s in disease_symptoms[disease]:
            if s not in asked:
                freq[s] = freq.get(s, 0) + 1

    if not freq:
        return None, possible

    next_q = max(freq, key=freq.get)
    return next_q, possible


def get_severity_question(symptom):
    return SEVERITY_QUESTIONS.get(symptom.strip() if symptom else symptom)


def get_severity_options(question):
    return SEVERITY_OPTIONS.get(question, [])


def get_possible_count(asked, positive, negative):
    asked    = [s.strip() for s in asked]
    positive = [s.strip() for s in positive]
    negative = [s.strip() for s in negative]
    count = 0
    for disease, symptoms in disease_symptoms.items():
        if (all(p in symptoms for p in positive) and
                not any(n in symptoms for n in negative)):
            count += 1
    return count