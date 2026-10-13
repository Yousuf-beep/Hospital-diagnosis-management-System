"""
pdf_report.py — Pre-Appointment PDF Report Generator
Uses ReportLab to create a professional, styled medical report
the patient can hand directly to their doctor.
"""

import io
import datetime
from xml.sax.saxutils import escape as xml_escape
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)

# ── Brand colours ──────────────────────────────────────────────────────────────
INK   = colors.HexColor("#0d1117")
TEAL  = colors.HexColor("#0a7c6e")
TEAL2 = colors.HexColor("#12b0a0")
RED   = colors.HexColor("#e8341c")
AMBER = colors.HexColor("#d97706")
GREEN = colors.HexColor("#16a34a")
CREAM = colors.HexColor("#f5f3ef")
MUTED = colors.HexColor("#6b6660")
WHITE = colors.white


def _risk_color(risk: int):
    if risk < 20:  return GREEN
    if risk < 40:  return AMBER
    return RED


def _risk_label(risk: int) -> str:
    if risk < 20:  return "Low"
    if risk < 40:  return "Moderate"
    return "High"


def _conf_color(conf: float):
    if conf >= 60: return GREEN
    if conf >= 35: return AMBER
    return RED


def _esc(value):
    """Recursively XML-escape every string inside lists / tuples / dicts."""
    if isinstance(value, str):
        return xml_escape(value)
    if isinstance(value, dict):
        return {k: _esc(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_esc(v) for v in value)
    return value


def generate_report(
    patient_name: str,
    age,
    gender: str,
    city: str,
    disease: str,
    confidence: float,
    risk: int,
    doctor: str,
    symptoms: list,
    predictions: list,
    precautions: list,
    description: str,
    diary: list = None,
    regional_alerts: list = None,
    severity_notes: dict = None,
    assigned_doctor: dict = None,
    generated_by: str = "MediAI",
) -> bytes:
    """Generate a PDF report and return as bytes."""

    # ReportLab Paragraphs parse markup, so user-supplied text (names, diary
    # notes, form fields) must be escaped or a stray "<" breaks the PDF.
    patient_name, gender, city, disease, doctor, description = (
        _esc(patient_name), _esc(gender), _esc(city), _esc(disease),
        _esc(doctor), _esc(description))
    symptoms, predictions, precautions = _esc(symptoms), _esc(predictions), _esc(precautions)
    diary, regional_alerts = _esc(diary), _esc(regional_alerts)
    severity_notes, assigned_doctor = _esc(severity_notes), _esc(assigned_doctor)

    buf    = io.BytesIO()
    W, H   = A4
    margin = 18 * mm

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=margin, rightMargin=margin,
        topMargin=14 * mm, bottomMargin=14 * mm,
    )

    styles = getSampleStyleSheet()

    # ── Custom paragraph styles ──
    def S(name, **kw):
        return ParagraphStyle(name, **kw)

    s_h1    = S("h1",    fontName="Helvetica-Bold",  fontSize=22, textColor=INK,   spaceAfter=2)
    s_h2    = S("h2",    fontName="Helvetica-Bold",  fontSize=13, textColor=INK,   spaceAfter=4, spaceBefore=10)
    s_h3    = S("h3",    fontName="Helvetica-Bold",  fontSize=10, textColor=MUTED, spaceAfter=4, spaceBefore=6)
    s_body  = S("body",  fontName="Helvetica",       fontSize=9,  textColor=INK,   leading=14,   spaceAfter=3)
    s_small = S("small", fontName="Helvetica",       fontSize=8,  textColor=MUTED, leading=12)
    s_label = S("label", fontName="Helvetica-Bold",  fontSize=7,  textColor=MUTED, spaceAfter=2,
                 textTransform="uppercase", letterSpacing=0.8)
    s_logo  = S("logo",  fontName="Helvetica-Bold",  fontSize=20, textColor=WHITE)
    s_right = S("right", fontName="Helvetica",       fontSize=8,  textColor=WHITE, alignment=TA_RIGHT)
    s_warn  = S("warn",  fontName="Helvetica-Bold",  fontSize=9,  textColor=RED,   leading=13)

    story = []
    now   = datetime.datetime.now().strftime("%d %B %Y, %I:%M %p")

    # ── HEADER BANNER ──────────────────────────────────────────────────────────
    header_data = [[
        Paragraph("MediAI", s_logo),
        Paragraph(f"Pre-Appointment Report<br/><font size='7'>{now}</font>", s_right),
    ]]
    header_tbl = Table(header_data, colWidths=[W - 2*margin - 60*mm, 60*mm])
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0,0), (-1,-1), INK),
        ("TOPPADDING",     (0,0), (-1,-1), 12),
        ("BOTTOMPADDING",  (0,0), (-1,-1), 12),
        ("LEFTPADDING",    (0,0), (0,-1),  14),
        ("RIGHTPADDING",  (-1,0), (-1,-1), 14),
        ("VALIGN",         (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 6*mm))

    # ── PATIENT INFO ───────────────────────────────────────────────────────────
    story.append(Paragraph("PATIENT INFORMATION", s_label))
    story.append(HRFlowable(width="100%", thickness=1, color=TEAL, spaceAfter=4))

    pat_items = [
        ["Full Name",  patient_name or "—"],
        ["Age",        str(age) + " years" if age else "—"],
        ["Gender",     gender or "—"],
        ["City",       city or "—"],
        ["Report ID",  f"MED-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"],
    ]
    pat_data = [[Paragraph(f"<b>{k}</b>", s_small), Paragraph(v, s_body)] for k,v in pat_items]
    pat_tbl  = Table(pat_data, colWidths=[40*mm, W - 2*margin - 40*mm])
    pat_tbl.setStyle(TableStyle([
        ("TOPPADDING",    (0,0),(-1,-1), 3),
        ("BOTTOMPADDING", (0,0),(-1,-1), 3),
        ("LEFTPADDING",   (0,0),(0,-1),  0),
        ("LINEBELOW",     (0,-1),(-1,-1), 0.5, colors.HexColor("#ede9e1")),
    ]))
    story.append(pat_tbl)
    story.append(Spacer(1, 5*mm))

    # ── PRIMARY DIAGNOSIS ─────────────────────────────────────────────────────
    story.append(Paragraph("AI DIAGNOSIS SUMMARY", s_label))
    story.append(HRFlowable(width="100%", thickness=1, color=TEAL, spaceAfter=4))

    diag_data = [[
        [
            Paragraph(disease, s_h1),
            Paragraph("Primary AI Prediction", s_small),
        ],
        [
            Paragraph(f"<font color='#{_conf_color(confidence).hexval()[2:]}'>"
                      f"<b>{confidence}%</b></font>", S("cf", fontName="Helvetica-Bold", fontSize=18, alignment=TA_CENTER)),
            Paragraph("Confidence", S("cfl", fontName="Helvetica", fontSize=7, textColor=MUTED, alignment=TA_CENTER)),
        ],
        [
            Paragraph(f"<font color='#{_risk_color(risk).hexval()[2:]}'>"
                      f"<b>{risk}</b></font>", S("rf", fontName="Helvetica-Bold", fontSize=18, alignment=TA_CENTER)),
            Paragraph(f"{_risk_label(risk)} Risk", S("rfl", fontName="Helvetica", fontSize=7, textColor=MUTED, alignment=TA_CENTER)),
        ],
    ]]
    diag_tbl = Table([[
        Paragraph(disease, s_h1),
        Paragraph(f"Confidence: <b><font color='#{_conf_color(confidence).hexval()[2:]}'>{confidence}%</font></b>  |  "
                  f"Risk: <b><font color='#{_risk_color(risk).hexval()[2:]}'>{_risk_label(risk)} ({risk})</font></b>  |  "
                  f"Specialist: <b>{doctor}</b>", s_body),
    ]], colWidths=[70*mm, W - 2*margin - 70*mm])
    diag_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), CREAM),
        ("TOPPADDING",    (0,0),(-1,-1), 10),
        ("BOTTOMPADDING", (0,0),(-1,-1), 10),
        ("LEFTPADDING",   (0,0),(0,-1),  12),
        ("RIGHTPADDING",  (-1,0),(-1,-1),12),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("BOX",           (0,0),(-1,-1), 0.5, TEAL),
    ]))
    story.append(diag_tbl)

    if confidence < 40:
        story.append(Spacer(1, 3*mm))
        story.append(Paragraph(
            "⚠  Low confidence prediction — please consult a doctor for professional evaluation.",
            s_warn
        ))

    story.append(Spacer(1, 5*mm))

    # ── DIFFERENTIAL DIAGNOSIS TABLE ──────────────────────────────────────────
    story.append(Paragraph("DIFFERENTIAL DIAGNOSIS (TOP 5)", s_label))
    story.append(HRFlowable(width="100%", thickness=1, color=TEAL, spaceAfter=4))

    pred_rows = [
        [Paragraph("<b>Rank</b>", s_small),
         Paragraph("<b>Condition</b>", s_small),
         Paragraph("<b>Probability</b>", s_small)]
    ]
    for i, (d, p) in enumerate(predictions[:5]):
        bar_pct = round(p * 100, 1)
        pred_rows.append([
            Paragraph(f"#{i+1}", s_body),
            Paragraph(d, s_body),
            Paragraph(f"{bar_pct}%", s_body),
        ])

    pred_tbl = Table(pred_rows, colWidths=[15*mm, 100*mm, 30*mm])
    pred_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), INK),
        ("TEXTCOLOR",     (0,0),(-1,0), WHITE),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 8),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, CREAM]),
        ("TOPPADDING",    (0,0),(-1,-1), 5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("GRID",          (0,0),(-1,-1), 0.3, colors.HexColor("#d4cfc8")),
    ]))
    story.append(pred_tbl)
    story.append(Spacer(1, 5*mm))

    # ── REPORTED SYMPTOMS ─────────────────────────────────────────────────────
    story.append(Paragraph("REPORTED SYMPTOMS", s_label))
    story.append(HRFlowable(width="100%", thickness=1, color=TEAL, spaceAfter=4))

    sym_items = []
    for s in symptoms:
        note = (severity_notes or {}).get(s, "")
        label = s.replace("_", " ").title()
        text  = f"<b>{label}</b>" + (f" — <i>{note}</i>" if note else "")
        sym_items.append(Paragraph(f"• {text}", s_body))

    story.append(KeepTogether(sym_items))
    story.append(Spacer(1, 5*mm))

    # ── REGIONAL ALERTS ───────────────────────────────────────────────────────
    if regional_alerts:
        story.append(Paragraph("REGIONAL DISEASE ALERTS", s_label))
        story.append(HRFlowable(width="100%", thickness=1, color=RED, spaceAfter=4))
        for alert in regional_alerts:
            level_color = RED if alert["level"] == "HIGH" else AMBER
            story.append(Paragraph(
                f"<b><font color='#{level_color.hexval()[2:]}'>[{alert['level']}]</font> "
                f"{alert['disease']}</b> — {', '.join(alert['reasons'])}",
                s_body
            ))
        story.append(Spacer(1, 4*mm))

    # ── ABOUT THIS CONDITION ──────────────────────────────────────────────────
    if description:
        story.append(Paragraph("ABOUT THIS CONDITION", s_label))
        story.append(HRFlowable(width="100%", thickness=1, color=TEAL, spaceAfter=4))
        story.append(Paragraph(description, s_body))
        story.append(Spacer(1, 5*mm))

    # ── PRECAUTIONS ───────────────────────────────────────────────────────────
    if precautions:
        story.append(Paragraph("RECOMMENDED PRECAUTIONS", s_label))
        story.append(HRFlowable(width="100%", thickness=1, color=TEAL, spaceAfter=4))
        for p in precautions:
            if p and p.strip():
                story.append(Paragraph(f"✓  {p.strip().capitalize()}", s_body))
        story.append(Spacer(1, 5*mm))

    # ── SYMPTOM DIARY TREND ───────────────────────────────────────────────────
    if diary and len(diary) >= 2:
        story.append(Paragraph("SYMPTOM DIARY (LAST 14 DAYS)", s_label))
        story.append(HRFlowable(width="100%", thickness=1, color=TEAL, spaceAfter=4))
        diary_rows = [[
            Paragraph("<b>Date</b>", s_small),
            Paragraph("<b>Symptoms</b>", s_small),
            Paragraph("<b>Severity</b>", s_small),
            Paragraph("<b>Notes</b>", s_small),
        ]]
        for d in diary[-10:]:  # last 10 entries
            diary_rows.append([
                Paragraph(d["date"], s_body),
                Paragraph(", ".join(s.replace("_"," ").title() for s in d["symptoms"][:3])
                          + ("…" if len(d["symptoms"]) > 3 else ""), s_body),
                Paragraph(str(d["severity"]) + "/5", s_body),
                Paragraph(d["notes"][:40] + ("…" if len(d["notes"]) > 40 else ""), s_small),
            ])
        diary_tbl = Table(diary_rows, colWidths=[25*mm, 75*mm, 20*mm, 30*mm])
        diary_tbl.setStyle(TableStyle([
            ("BACKGROUND",     (0,0),(-1,0), INK),
            ("TEXTCOLOR",      (0,0),(-1,0), WHITE),
            ("FONTNAME",       (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",       (0,0),(-1,-1), 7.5),
            ("ROWBACKGROUNDS", (0,1),(-1,-1), [WHITE, CREAM]),
            ("TOPPADDING",     (0,0),(-1,-1), 4),
            ("BOTTOMPADDING",  (0,0),(-1,-1), 4),
            ("LEFTPADDING",    (0,0),(-1,-1), 6),
            ("GRID",           (0,0),(-1,-1), 0.3, colors.HexColor("#d4cfc8")),
        ]))
        story.append(diary_tbl)
        story.append(Spacer(1, 5*mm))

    # ── ASSIGNED DOCTOR (in-app) ──────────────────────────────────────────────
    story.append(Paragraph("ASSIGNED DOCTOR", s_label))
    story.append(HRFlowable(width="100%", thickness=1, color=TEAL, spaceAfter=4))

    if assigned_doctor:
        doc_name   = assigned_doctor.get("full_name", "—")
        doc_spec   = assigned_doctor.get("specialty", doctor)
        doc_qual   = assigned_doctor.get("qualification", "")
        doc_exp    = assigned_doctor.get("experience_yrs", "")
        doc_detail = []
        if doc_qual:
            doc_detail.append(doc_qual)
        if doc_exp:
            doc_detail.append(f"{doc_exp} years experience")
        detail_str = "  •  ".join(doc_detail) if doc_detail else "MediAI Registered Physician"

        doc_tbl = Table([[
            Paragraph(f"<b>{doc_name}</b>", s_h2),
            Paragraph(
                f"<b>{doc_spec}</b><br/>"
                f"<font size='8' color='#6b6660'>{detail_str}</font><br/>"
                f"<font size='8' color='#12b0a0'>Assigned via MediAI — view at /my_consultations</font>",
                s_body
            ),
        ]], colWidths=[60*mm, W - 2*margin - 60*mm])
    else:
        doc_tbl = Table([[
            Paragraph(f"<b>{doctor}</b>", s_h2),
            Paragraph(
                f"Specialty: <b>{doctor}</b><br/>"
                f"<font size='8' color='#6b6660'>No doctor assigned yet — login to get assigned automatically.</font>",
                s_body
            ),
        ]], colWidths=[60*mm, W - 2*margin - 60*mm])

    doc_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), colors.HexColor("#0d1117")),
        ("TEXTCOLOR",     (0,0),(-1,-1), WHITE),
        ("TOPPADDING",    (0,0),(-1,-1), 10),
        ("BOTTOMPADDING", (0,0),(-1,-1), 10),
        ("LEFTPADDING",   (0,0),(0,-1),  12),
        ("RIGHTPADDING",  (-1,0),(-1,-1),12),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("BOX",           (0,0),(-1,-1), 0.5, TEAL),
    ]))
    story.append(doc_tbl)
    story.append(Spacer(1, 5*mm))

    # ── DISCLAIMER ────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#d4cfc8")))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        "DISCLAIMER: This report is generated by an AI system (MediAI) for informational purposes only. "
        "It does not constitute medical advice, diagnosis, or treatment. Always consult a licensed "
        "healthcare professional before making any medical decisions.",
        S("disc", fontName="Helvetica", fontSize=7, textColor=MUTED, leading=10)
    ))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        f"Generated by MediAI Health Diagnostic System  •  {now}",
        S("foot", fontName="Helvetica", fontSize=7, textColor=MUTED, alignment=TA_CENTER)
    ))

    doc.build(story)
    return buf.getvalue()