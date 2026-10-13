"""
symptom_diary.py — Symptom Diary / Timeline feature
Logs daily symptoms per user so trends can be visualized over time.
The symptom_logs table itself is created in database.py.
"""

import json
from database import query, execute


def log_symptoms(user_id: int, log_date: str, symptoms: list,
                 severity: int = 1, notes: str = ""):
    """Insert or update a symptom log for a given date."""
    execute("""
        INSERT INTO symptom_logs (user_id, log_date, symptoms, severity, notes)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, log_date) DO UPDATE SET
            symptoms = excluded.symptoms,
            severity = excluded.severity,
            notes    = excluded.notes
    """, (user_id, log_date, json.dumps(symptoms), severity, notes))


def get_diary(user_id: int, days: int = 14) -> list:
    """Return symptom logs for the last N days."""
    rows = query("""
        SELECT log_date, symptoms, severity, notes
        FROM   symptom_logs
        WHERE  user_id  = ?
          AND  log_date >= date('now','localtime', ?)
        ORDER  BY log_date ASC
    """, (user_id, f"-{int(days)} days"))
    return [{
        "date":     str(r["log_date"]),
        "symptoms": json.loads(r["symptoms"]),
        "severity": r["severity"],
        "notes":    r["notes"] or "",
    } for r in rows]


def get_trend_data(user_id: int, days: int = 14) -> dict:
    """
    Returns chart-ready data:
    - dates list
    - severity list
    - symptom_counts list
    - most_common symptoms
    """
    diary = get_diary(user_id, days)
    if not diary:
        return {"dates": [], "severity": [], "counts": [], "common": []}

    dates     = [d["date"] for d in diary]
    severity  = [d["severity"] for d in diary]
    counts    = [len(d["symptoms"]) for d in diary]

    # Most common symptoms over the period
    freq = {}
    for d in diary:
        for s in d["symptoms"]:
            freq[s] = freq.get(s, 0) + 1
    common = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "dates":    dates,
        "severity": severity,
        "counts":   counts,
        "common":   [{"symptom": s, "count": c} for s, c in common],
    }
