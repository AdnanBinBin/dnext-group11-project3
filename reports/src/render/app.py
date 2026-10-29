"""Etape 3 du workflow : graphiques + mise en page PDF, puis depot dans S3.

Code deterministe : le contenu redactionnel vient de Bedrock (etape narrate),
les chiffres et les graphiques viennent uniquement des donnees collectees.
"""
import io
import json
import os
from datetime import datetime, timezone

import boto3
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from fpdf import FPDF  # noqa: E402
from fpdf.enums import XPos, YPos  # noqa: E402

BUCKET = os.environ["REPORTS_BUCKET"]
ANOMALY_REASONS = ("CIF_BELOW_FOB", "GAP_ABOVE_35")
FONT_DIR = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")

NAVY = (23, 55, 94)
RED = (198, 40, 40)
GREY = (110, 110, 110)
FOB_COLOR, CIF_COLOR, FLAG_COLOR, NEUTRAL_COLOR = "#1f5f99", "#d17a00", "#c62828", "#9e9e9e"

s3 = boto3.client("s3")

# Textes du PDF, écrits pour un lecteur métier (export/import), sans vocabulaire technique
LABELS = {
    "fr": {
        "doc_title": "Analyse d'un écart de déclaration",
        "subtitle": "Comparaison des déclarations à l'export et à l'import (source : UN Comtrade)",
        "flow": "Échange concerné",
        "exporter": "Pays exportateur", "importer": "Pays importateur", "product": "Produit",
        "hs": "code douanier SH", "period": "Période", "rule": "Motif du signalement",
        "figures": "Chiffres déclarés",
        "export_side": "Déclaré à l'export (FOB)", "import_side": "Déclaré à l'import (CIF)",
        "value": "Valeur (USD)", "weight": "Quantité (tonnes)", "unit_price": "Prix (USD par tonne)",
        "gap": "Écart entre le prix à l'import et le prix à l'export",
        "weight_ratio": "Quantité déclarée à l'import pour 1 tonne déclarée à l'export",
        "summary": "Synthèse", "charts": "Évolution et comparaison",
        "findings": "Constats", "explanations": "Explications possibles",
        "evidence": "Ce qui l'appuie", "verify": "Comment le vérifier",
        "limitations": "Limites de l'analyse", "actions": "Actions recommandées",
        "likelihood": {"high": "probable", "medium": "possible", "low": "peu probable"},
        "categories": {
            "rerouting_transshipment": "Réacheminement ou transbordement",
            "conflict_disruption": "Conflit ou perturbation logistique",
            "tariffs_sanctions": "Droits de douane ou sanctions",
            "data_entry_error": "Erreur de saisie ou de déclaration",
            "valuation_or_product_mix": "Qualité ou valorisation différente du produit",
            "other": "Autre",
        },
        "rules": {
            "CIF_BELOW_FOB": "Le prix déclaré à l'import est inférieur au prix déclaré à l'export, "
                             "alors qu'il devrait inclure le fret et l'assurance.",
            "GAP_ABOVE_35": "Le prix déclaré à l'import dépasse le prix déclaré à l'export de plus de "
                            "{threshold}, davantage que ne l'expliquent le fret et l'assurance.",
            "OK": "Aucun : les déclarations concordent.",
        },
        "fob": "Export (FOB)", "cif": "Import (CIF)",
        "chart_price": "Prix (USD par tonne)", "chart_gap": "Écart import / export (%)", "threshold": "seuil",
        "chart_peers": "Prix à l'export vers les autres pays clients, même période (USD par tonne)",
        "generated": "Rédigé le", "report_id": "Référence",
        "footer": "Analyse rédigée automatiquement par intelligence artificielle, à vérifier avant toute décision",
        "filename": "analyse-ecart",
        "months": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                   "septembre", "octobre", "novembre", "décembre"],
        "short_months": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août",
                         "sept.", "oct.", "nov.", "déc."],
        "decimal": ",", "thousands": " ",
    },
    "en": {
        "doc_title": "Declaration gap analysis",
        "subtitle": "Comparison of export and import declarations (source: UN Comtrade)",
        "flow": "Trade flow",
        "exporter": "Exporting country", "importer": "Importing country", "product": "Product",
        "hs": "HS customs code", "period": "Period", "rule": "Reason for the alert",
        "figures": "Declared figures",
        "export_side": "Declared at export (FOB)", "import_side": "Declared at import (CIF)",
        "value": "Value (USD)", "weight": "Quantity (tonnes)", "unit_price": "Price (USD per tonne)",
        "gap": "Gap between import and export price",
        "weight_ratio": "Quantity declared at import per tonne declared at export",
        "summary": "Summary", "charts": "Trend and comparison",
        "findings": "Findings", "explanations": "Possible explanations",
        "evidence": "Supporting evidence", "verify": "How to check",
        "limitations": "Limits of this analysis", "actions": "Recommended actions",
        "likelihood": {"high": "likely", "medium": "possible", "low": "unlikely"},
        "categories": {
            "rerouting_transshipment": "Rerouting or transshipment",
            "conflict_disruption": "Conflict or logistics disruption",
            "tariffs_sanctions": "Customs duties or sanctions",
            "data_entry_error": "Data-entry or declaration error",
            "valuation_or_product_mix": "Different product quality or valuation",
            "other": "Other",
        },
        "rules": {
            "CIF_BELOW_FOB": "The import price is below the export price, although it should include "
                             "freight and insurance.",
            "GAP_ABOVE_35": "The import price exceeds the export price by more than {threshold}, "
                            "more than freight and insurance explain.",
            "OK": "None: the declarations match.",
        },
        "fob": "Export (FOB)", "cif": "Import (CIF)",
        "chart_price": "Price (USD per tonne)", "chart_gap": "Import / export gap (%)", "threshold": "threshold",
        "chart_peers": "Export price to the other customer countries, same period (USD per tonne)",
        "generated": "Written on", "report_id": "Reference",
        "footer": "Analysis written automatically by artificial intelligence, to be checked before any decision",
        "filename": "gap-analysis",
        "months": ["January", "February", "March", "April", "May", "June", "July", "August",
                   "September", "October", "November", "December"],
        "short_months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        "decimal": ".", "thousands": ",",
    },
}


def fmt(value, labels, decimals=0, suffix=""):
    if value is None:
        return "–"
    text = f"{value:,.{decimals}f}".replace(",", "§").replace(".", labels["decimal"])
    return text.replace("§", labels["thousands"]) + suffix


def per_tonne(usd_per_kg):
    return None if usd_per_kg is None else usd_per_kg * 1000


def tonnes(kg):
    return None if kg is None else kg / 1000


def period_text(period, labels, short=False):
    """'2021-10' -> 'octobre 2021' (ou 'oct. 21' pour les axes) ; une année reste telle quelle."""
    period = str(period)
    if len(period) == 7 and period[4] == "-" and period[5:].isdigit():
        month = int(period[5:]) - 1
        if short:
            return f"{labels['short_months'][month]} {period[2:4]}"
        return f"{labels['months'][month]} {period[:4]}"
    return period


def date_text(moment, labels):
    return f"{moment.day} {labels['months'][moment.month - 1]} {moment.year}"


def percent(fraction, labels, decimals=0, signed=False):
    if fraction is None:
        return "–"
    sign = ("+" if fraction >= 0 else "−") if signed else ""
    return sign + fmt(abs(fraction) * 100 if signed else fraction * 100, labels, decimals, " %")


# ---------- Graphiques ----------

def png(fig):
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return buffer


def history_chart(context, labels):
    anomaly = context["anomaly"]
    rows = [h for h in context["history"] if h.get("fob_unit_price") and h.get("cif_unit_price")]
    if not rows and anomaly.get("fob_unit_price") and anomaly.get("cif_unit_price"):
        rows = [anomaly]
    if not rows:
        return None

    periods = [period_text(r["period"], labels, short=True) for r in rows]
    x = list(range(len(rows)))
    flagged = [r.get("flag") in ANOMALY_REASONS for r in rows]
    current = [str(r["period"]) == str(anomaly["period"]) for r in rows]
    threshold = context["rule"]["gap_threshold"] * 100

    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(7.5, 4.6), sharex=True, gridspec_kw={"height_ratios": [3, 2]}
    )
    top.plot(x, [per_tonne(r["fob_unit_price"]) for r in rows], marker="o", color=FOB_COLOR, label=labels["fob"])
    top.plot(x, [per_tonne(r["cif_unit_price"]) for r in rows], marker="s", color=CIF_COLOR, label=labels["cif"])
    top.set_ylabel(labels["chart_price"], fontsize=8)
    top.legend(fontsize=8, frameon=False)

    gaps = [(r["gap"] or 0) * 100 for r in rows]
    bottom.bar(x, gaps, color=[FLAG_COLOR if f else NEUTRAL_COLOR for f in flagged], width=0.55)
    bottom.axhline(threshold, color=FLAG_COLOR, linestyle="--", linewidth=1)
    bottom.text(x[-1] + 0.35, threshold, f"{labels['threshold']} {threshold:.0f} %",
                color=FLAG_COLOR, fontsize=7, va="bottom", ha="right")
    bottom.axhline(0, color="black", linewidth=0.8)
    bottom.set_ylabel(labels["chart_gap"], fontsize=8)
    bottom.set_xticks(x, periods)
    if len(periods) > 8:  # historique mensuel Gold : jusqu'à 25 mois
        bottom.tick_params(axis="x", rotation=60)

    for i, is_current in enumerate(current):
        if is_current:
            for ax in (top, bottom):
                ax.axvspan(i - 0.4, i + 0.4, color=FLAG_COLOR, alpha=0.08, linewidth=0)
    for ax in (top, bottom):
        ax.grid(axis="y", alpha=0.3)
        ax.tick_params(labelsize=8)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return png(fig)


def peers_chart(context, labels):
    anomaly = context["anomaly"]
    peers = [p for p in context["peers_same_period"] if p.get("fob_unit_price")]
    if len(peers) < 2:
        return None
    peers = sorted(peers, key=lambda p: p["fob_unit_price"])
    names = [p.get("partner_name") or p["partner"] for p in peers]
    values = [per_tonne(p["fob_unit_price"]) for p in peers]
    colors = [FLAG_COLOR if p["partner"] == anomaly["partner"] else FOB_COLOR for p in peers]

    fig, ax = plt.subplots(figsize=(7.5, 0.35 * len(peers) + 0.8))
    bars = ax.barh(names, values, color=colors, height=0.6)
    ax.bar_label(bars, fmt="%.0f", fontsize=7, padding=3)
    ax.set_xlim(min(values) * 0.9, max(values) * 1.06)
    ax.set_title(labels["chart_peers"], fontsize=9, loc="left")
    ax.tick_params(labelsize=8)
    ax.grid(axis="x", alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return png(fig)


# ---------- PDF ----------

class ReportPDF(FPDF):
    def __init__(self, footer_text):
        super().__init__(format="A4")
        self.footer_text = footer_text
        self.add_font("DejaVu", "", os.path.join(FONT_DIR, "DejaVuSans.ttf"))
        self.add_font("DejaVu", "B", os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf"))
        self.add_font("DejaVu", "I", os.path.join(FONT_DIR, "DejaVuSans-Oblique.ttf"))
        self.set_margins(18, 16, 18)
        self.set_auto_page_break(True, margin=18)

    def footer(self):
        self.set_y(-12)
        self.set_font("DejaVu", "I", 7)
        self.set_text_color(*GREY)
        self.cell(0, 5, f"{self.footer_text}   ·   {self.page_no()}/{{nb}}", align="C")

    def heading(self, text, keep_with_next=25):
        """Titre de section, renvoye a la page suivante s'il resterait seul en bas de page."""
        if self.get_y() > self.page_break_trigger - keep_with_next:
            self.add_page()
        self.ln(4)
        self.set_font("DejaVu", "B", 12)
        self.set_text_color(*NAVY)
        self.cell(0, 7, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*NAVY)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)
        self.set_text_color(30, 30, 30)

    def paragraph(self, text, size=9.5, style=""):
        self.set_font("DejaVu", style, size)
        self.multi_cell(0, 5, text, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def bullets(self, items):
        self.set_font("DejaVu", "", 9.5)
        for item in items:
            self.set_x(self.l_margin + 2)
            self.multi_cell(0, 5, f"•  {item}", align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.ln(0.8)

    def labelled(self, label, text, indent=6):
        margin = self.l_margin
        self.set_left_margin(margin + indent)
        self.set_x(margin + indent)
        self.set_font("DejaVu", "B", 9)
        self.write(5, f"{label} : ")
        self.set_font("DejaVu", "", 9)
        self.write(5, text)
        self.ln(5.5)
        self.set_left_margin(margin)


def build_pdf(state, labels):
    context = state["context"]
    narrative = state["narrative"]
    report = narrative["report"]
    anomaly = context["anomaly"]
    rule = context["rule"]

    pdf = ReportPDF(labels["footer"])
    pdf.set_title(report["title"])
    pdf.add_page()

    # En-tete
    pdf.set_font("DejaVu", "B", 17)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(0, 8, labels["doc_title"], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("DejaVu", "", 8.5)
    pdf.set_text_color(*GREY)
    generated = date_text(datetime.now(timezone.utc), labels)
    pdf.multi_cell(0, 4.5, f"{labels['subtitle']}\n{labels['generated']} {generated}, "
                           f"{labels['report_id'].lower()} {state['report_id'][:8].upper()}",
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)
    pdf.set_text_color(*RED)
    pdf.paragraph(report["title"], size=12, style="B")

    # Echange concerne
    pdf.heading(labels["flow"])
    threshold = percent(rule["gap_threshold"], labels)
    rule_text = labels["rules"].get(rule["triggered"], rule["triggered"] or "–").format(threshold=threshold)
    product = anomaly.get("product_name") or anomaly.get("product") or ""
    pdf.set_font("DejaVu", "", 9)
    with pdf.table(col_widths=(40, 130), first_row_as_headings=False, line_height=5.5,
                   text_align="LEFT", borders_layout="HORIZONTAL_LINES") as table:
        for key, value in (
            ("exporter", anomaly.get("reporter_name") or anomaly["reporter"]),
            ("importer", anomaly.get("partner_name") or anomaly["partner"]),
            ("product", f"{product} ({labels['hs']} {anomaly['hs_code']})"),
            ("period", period_text(anomaly["period"], labels)),
            ("rule", rule_text),
        ):
            row = table.row()
            row.cell(labels[key])
            row.cell(str(value))

    # Chiffres declares
    pdf.heading(labels["figures"])
    pdf.set_font("DejaVu", "", 9)
    with pdf.table(col_widths=(50, 60, 60), text_align=("LEFT", "RIGHT", "RIGHT"), line_height=5.5,
                   borders_layout="HORIZONTAL_LINES") as table:
        header = table.row()
        for text in ("", labels["export_side"], labels["import_side"]):
            header.cell(text)
        for label, export_value, import_value in (
            (labels["value"], fmt(anomaly["export_value_usd"], labels), fmt(anomaly["import_value_usd"], labels)),
            (labels["weight"], fmt(tonnes(anomaly["export_weight_kg"]), labels),
             fmt(tonnes(anomaly["import_weight_kg"]), labels)),
            (labels["unit_price"], fmt(per_tonne(anomaly["fob_unit_price"]), labels, 1),
             fmt(per_tonne(anomaly["cif_unit_price"]), labels, 1)),
        ):
            row = table.row()
            row.cell(label)
            row.cell(export_value)
            row.cell(import_value)
    pdf.ln(2)
    separator = " : " if labels["decimal"] == "," else ": "
    pdf.set_text_color(*RED)
    pdf.paragraph(f"{labels['gap']}{separator}{percent(anomaly.get('gap'), labels, 1, signed=True)}", style="B")
    pdf.set_text_color(30, 30, 30)
    if anomaly.get("weight_ratio_import_export") is not None:
        pdf.paragraph(f"{labels['weight_ratio']}{separator}"
                      f"{fmt(anomaly['weight_ratio_import_export'], labels, 2)} t", size=9)

    pdf.heading(labels["summary"])
    pdf.paragraph(report["summary"], size=10)

    charts = [c for c in (history_chart(context, labels), peers_chart(context, labels)) if c]
    if charts:
        pdf.heading(labels["charts"], keep_with_next=pdf.epw * 0.62)
        for chart in charts:
            pdf.image(chart, w=pdf.epw)
            pdf.ln(2)

    pdf.heading(labels["findings"])
    pdf.bullets(report["key_findings"])

    pdf.heading(labels["explanations"])
    for i, item in enumerate(report["explanations"], start=1):
        if pdf.get_y() > pdf.page_break_trigger - 30:
            pdf.add_page()
        category = labels["categories"].get(item["category"], item["category"])
        likelihood = labels["likelihood"].get(item["likelihood"], item["likelihood"])
        pdf.set_text_color(*NAVY)
        pdf.paragraph(f"{i}. {category} ({likelihood})", size=10, style="B")
        pdf.set_text_color(30, 30, 30)
        pdf.set_x(pdf.l_margin + 6)
        pdf.paragraph(item["hypothesis"])
        pdf.labelled(labels["evidence"], item["supporting_evidence"])
        pdf.labelled(labels["verify"], item["how_to_verify"])
        pdf.ln(2)

    limitations = list(context.get("warnings", [])) + list(report.get("data_limitations", []))
    if limitations:
        pdf.heading(labels["limitations"])
        pdf.bullets(limitations)

    if report.get("recommended_actions"):
        pdf.heading(labels["actions"])
        pdf.bullets(report["recommended_actions"])

    return bytes(pdf.output())


def handler(event, _context):
    labels = LABELS.get(event.get("language"), LABELS["fr"])
    report_id = event["report_id"]
    prefix = f"reports/{report_id}"

    pdf_bytes = build_pdf(event, labels)
    s3.put_object(Bucket=BUCKET, Key=f"{prefix}/report.pdf", Body=pdf_bytes,
                  ContentType="application/pdf",
                  ContentDisposition=f'inline; filename="{labels["filename"]}-{report_id[:8]}.pdf"')

    record = {
        "report_id": report_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "language": event.get("language"),
        "context": event["context"],
        "report": event["narrative"]["report"],
        "model_id": event["narrative"]["model_id"],
        "usage": event["narrative"]["usage"],
    }
    s3.put_object(Bucket=BUCKET, Key=f"{prefix}/report.json",
                  Body=json.dumps(record, ensure_ascii=False, indent=1).encode(),
                  ContentType="application/json")

    return {
        "pdf_key": f"{prefix}/report.pdf",
        "json_key": f"{prefix}/report.json",
        "title": event["narrative"]["report"]["title"],
    }
