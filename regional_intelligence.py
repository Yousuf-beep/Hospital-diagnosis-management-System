"""
regional_intelligence.py — Regional Disease Intelligence for India
Based on IDSP historical outbreak data and seasonal disease patterns.
Boosts ML predictions when certain diseases are regionally prevalent.
"""

import datetime

# ── Seasonal disease calendar (month → diseases known to spike in India) ──────
SEASONAL = {
    1:  ["Common Cold", "Pneumonia", "Bronchial Asthma"],          # Jan — winter
    2:  ["Common Cold", "Pneumonia", "Bronchial Asthma"],          # Feb
    3:  ["Allergy", "Fungal infection", "Chicken pox"],            # Mar — spring
    4:  ["Allergy", "Chicken pox", "Typhoid"],                     # Apr — hot
    5:  ["Typhoid", "Jaundice", "Hepatitis A"],                    # May — hot/dry
    6:  ["Malaria", "Dengue", "Typhoid", "Hepatitis A", "Cholera"],# Jun — monsoon
    7:  ["Malaria", "Dengue", "Gastroenteritis", "Typhoid"],       # Jul
    8:  ["Malaria", "Dengue", "Gastroenteritis", "Leptospirosis"], # Aug
    9:  ["Malaria", "Dengue", "Chikungunya"],                      # Sep
    10: ["Dengue", "Chikungunya", "Malaria"],                      # Oct
    11: ["Common Cold", "Dengue", "Typhoid"],                      # Nov
    12: ["Common Cold", "Pneumonia", "Bronchial Asthma"],          # Dec
}

# ── City / State disease prevalence (based on IDSP historical data) ───────────
# Format: city_key → {disease: boost_score (0.0–1.0)}
REGIONAL_DATA = {
    # Karnataka
    "bengaluru":   {"Dengue": 0.9, "Malaria": 0.7, "Typhoid": 0.7, "Chikungunya": 0.8, "Zika": 0.4},
    "mysuru":      {"Malaria": 0.8, "Dengue": 0.7, "Typhoid": 0.6},
    "karnataka":   {"Dengue": 0.8, "Malaria": 0.7, "Chikungunya": 0.6},

    # Maharashtra
    "mumbai":      {"Malaria": 0.9, "Dengue": 0.85, "Leptospirosis": 0.8, "Typhoid": 0.7, "Hepatitis A": 0.6},
    "pune":        {"Dengue": 0.9, "Malaria": 0.7, "Zika": 0.6, "Chikungunya": 0.7},
    "nagpur":      {"Malaria": 0.85, "Dengue": 0.7, "Chikungunya": 0.65},
    "maharashtra": {"Malaria": 0.85, "Dengue": 0.8, "Leptospirosis": 0.6},

    # Delhi / NCR
    "delhi":       {"Dengue": 0.85, "Chikungunya": 0.75, "Malaria": 0.7, "Typhoid": 0.65},
    "new delhi":   {"Dengue": 0.85, "Chikungunya": 0.75, "Malaria": 0.7},
    "gurugram":    {"Dengue": 0.8, "Malaria": 0.65},
    "noida":       {"Dengue": 0.8, "Malaria": 0.65},

    # Uttar Pradesh
    "lucknow":     {"Dengue": 0.8, "Malaria": 0.75, "Typhoid": 0.7, "Japanese Encephalitis": 0.5},
    "varanasi":    {"Malaria": 0.8, "Typhoid": 0.75, "Hepatitis A": 0.65},
    "uttar pradesh": {"Malaria": 0.8, "Dengue": 0.7, "Japanese Encephalitis": 0.55},

    # Rajasthan
    "jaipur":      {"Malaria": 0.8, "Dengue": 0.7, "Chikungunya": 0.6},
    "jodhpur":     {"Malaria": 0.75, "Dengue": 0.6},
    "rajasthan":   {"Malaria": 0.8, "Dengue": 0.65},

    # Tamil Nadu
    "chennai":     {"Dengue": 0.85, "Malaria": 0.7, "Typhoid": 0.65, "Chikungunya": 0.7},
    "coimbatore":  {"Dengue": 0.75, "Malaria": 0.65},
    "tamil nadu":  {"Dengue": 0.8, "Malaria": 0.7, "Chikungunya": 0.65},

    # West Bengal
    "kolkata":     {"Malaria": 0.85, "Dengue": 0.8, "Typhoid": 0.7, "Hepatitis A": 0.6},
    "west bengal": {"Malaria": 0.85, "Dengue": 0.75, "Japanese Encephalitis": 0.5},

    # Kerala
    "thiruvananthapuram": {"Dengue": 0.8, "Leptospirosis": 0.75, "Malaria": 0.6},
    "kochi":       {"Dengue": 0.8, "Leptospirosis": 0.7, "Nipah": 0.3},
    "kerala":      {"Dengue": 0.8, "Leptospirosis": 0.75, "Nipah": 0.3, "Malaria": 0.6},

    # Gujarat
    "ahmedabad":   {"Malaria": 0.8, "Dengue": 0.75, "Chikungunya": 0.65},
    "surat":       {"Malaria": 0.8, "Dengue": 0.7},
    "gujarat":     {"Malaria": 0.8, "Dengue": 0.7, "Zika": 0.4},

    # Telangana / AP
    "hyderabad":   {"Dengue": 0.85, "Malaria": 0.7, "Typhoid": 0.65},
    "telangana":   {"Dengue": 0.8, "Malaria": 0.75},

    # Odisha
    "bhubaneswar": {"Malaria": 0.9, "Dengue": 0.7, "Japanese Encephalitis": 0.55},
    "odisha":      {"Malaria": 0.9, "Japanese Encephalitis": 0.6, "Dengue": 0.7},

    # Bihar / Jharkhand
    "patna":       {"Malaria": 0.85, "Kala-azar": 0.7, "Typhoid": 0.7},
    "bihar":       {"Malaria": 0.85, "Kala-azar": 0.75, "Japanese Encephalitis": 0.55},

    # Punjab / Haryana
    "chandigarh":  {"Dengue": 0.75, "Malaria": 0.65, "Chikungunya": 0.6},
    "amritsar":    {"Dengue": 0.7, "Malaria": 0.65},
    "punjab":      {"Dengue": 0.7, "Malaria": 0.65},
}

# ── Emergency triage rules ─────────────────────────────────────────────────────
EMERGENCY_COMBOS = [
    {
        "name": "Possible Cardiac Event",
        "symptoms": {"chest_pain", "breathlessness", "sweating"},
        "level": "CRITICAL",
        "action": "Call 112 immediately. Do not drive yourself. Possible heart attack.",
        "icon": "fa-heart-pulse",
    },
    {
        "name": "Severe Respiratory Distress",
        "symptoms": {"breathlessness", "chest_pain", "cough"},
        "level": "CRITICAL",
        "action": "Seek emergency care immediately. Possible severe respiratory condition.",
        "icon": "fa-lungs",
    },
    {
        "name": "Possible Stroke",
        "symptoms": {"headache", "vomiting", "loss_of_balance"},
        "level": "CRITICAL",
        "action": "Call 112 now. Note the time symptoms started. Do not eat or drink.",
        "icon": "fa-brain",
    },
    {
        "name": "Severe Dengue Warning",
        "symptoms": {"high_fever", "vomiting", "skin_rash", "fatigue"},
        "level": "HIGH",
        "action": "Go to hospital immediately. Severe dengue can cause internal bleeding.",
        "icon": "fa-virus",
    },
    {
        "name": "Possible Meningitis",
        "symptoms": {"high_fever", "headache", "stiff_neck"},
        "level": "CRITICAL",
        "action": "Emergency care needed immediately. Bacterial meningitis is life-threatening.",
        "icon": "fa-head-side-virus",
    },
    {
        "name": "Diabetic Emergency",
        "symptoms": {"fatigue", "vomiting", "excessive_hunger", "sweating"},
        "level": "HIGH",
        "action": "Check blood sugar immediately. If unconscious, call 112.",
        "icon": "fa-syringe",
    },
    {
        "name": "Severe Dehydration",
        "symptoms": {"vomiting", "diarrhoea", "fatigue"},
        "level": "MODERATE",
        "action": "Seek medical attention. Drink ORS solution. IV fluids may be needed.",
        "icon": "fa-droplet-slash",
    },
]


def get_city_key(city: str) -> str:
    if not city:
        return ""
    return city.lower().strip()


def get_regional_alerts(city: str, predictions: list) -> list:
    """
    Returns list of diseases that are both:
    - In the top predictions
    - Regionally/seasonally prevalent for the user's location
    """
    if not city:
        return []

    key      = get_city_key(city)
    month    = datetime.datetime.now().month
    seasonal = set(SEASONAL.get(month, []))

    # Find matching regional data
    regional = {}
    for loc_key, data in REGIONAL_DATA.items():
        if loc_key in key or key in loc_key:
            regional.update(data)

    alerts = []
    pred_diseases = {d for d, _ in predictions[:5]}

    for disease in pred_diseases:
        reasons = []
        boost   = 0

        if disease in seasonal:
            reasons.append(f"currently in season (month {month})")
            boost = max(boost, 0.5)

        if disease in regional:
            score = regional[disease]
            reasons.append(f"historically prevalent in {city.title()}")
            boost = max(boost, score)

        if reasons:
            alerts.append({
                "disease": disease,
                "reasons": reasons,
                "boost":   boost,
                "level":   "HIGH" if boost >= 0.75 else "MODERATE",
            })

    alerts.sort(key=lambda x: x["boost"], reverse=True)
    return alerts[:3]


def check_emergency(symptoms: list) -> dict | None:
    """
    Check if symptoms match any emergency combination.
    Returns the most severe match, or None.
    """
    sym_set = set(symptoms)
    matches = []

    for rule in EMERGENCY_COMBOS:
        overlap = rule["symptoms"] & sym_set
        if len(overlap) >= 2:  # at least 2 of the trigger symptoms
            matches.append({**rule, "matched": list(overlap)})

    if not matches:
        return None

    # Return highest severity match
    level_order = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2}
    matches.sort(key=lambda x: level_order.get(x["level"], 3))
    return matches[0]