"""Etape 2 du workflow : Bedrock redige l'explication de l'anomalie.

Le modele ne produit que du contenu structure (via un outil impose, donc du JSON
valide) ; la mise en page et les graphiques restent du code deterministe (render).
"""
import json
import os
import re

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

MODEL_ID = os.environ["MODEL_ID"]
DEFAULT_LANGUAGE = os.environ.get("REPORT_LANGUAGE", "fr")
LANGUAGE_NAMES = {"fr": "French", "en": "English"}
RETRYABLE = {"ThrottlingException", "ServiceUnavailableException", "ModelNotReadyException"}

bedrock = boto3.client(
    "bedrock-runtime",
    config=Config(read_timeout=100, retries={"max_attempts": 3, "mode": "adaptive"}),
)


class BedrockThrottled(Exception):
    """Levee pour que Step Functions refasse une tentative plus tard."""


class IncompleteReport(Exception):
    """Sortie du modele inexploitable (tronquee, outil non appele) : Step Functions reessaie une fois."""


CATEGORIES = [
    "rerouting_transshipment",
    "conflict_disruption",
    "tariffs_sanctions",
    "data_entry_error",
    "valuation_or_product_mix",
    "other",
]

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short title: product, exporting country to importing country, "
                                                     "month and year in words, then the gap in a few words."},
        "summary": {"type": "string", "description": "3 to 5 sentences: what was flagged, by how much, most likely reading."},
        "key_findings": {
            "type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 6,
            "description": "Factual observations derived from the figures provided.",
        },
        "explanations": {
            "type": "array", "minItems": 1, "maxItems": 4,
            "description": "Candidate explanations, most likely first.",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": CATEGORIES},
                    "hypothesis": {"type": "string"},
                    "likelihood": {"type": "string", "enum": ["high", "medium", "low"]},
                    "supporting_evidence": {"type": "string", "description": "Which provided figures support or weaken it."},
                    "how_to_verify": {"type": "string"},
                },
                "required": ["category", "hypothesis", "likelihood", "supporting_evidence", "how_to_verify"],
            },
        },
        "data_limitations": {"type": "array", "items": {"type": "string"}},
        "recommended_actions": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
    "required": ["title", "summary", "key_findings", "explanations", "data_limitations", "recommended_actions"],
}
EXPLANATION_FIELDS = set(REPORT_SCHEMA["properties"]["explanations"]["items"]["properties"])
ESSENTIAL_FIELDS = ("title", "summary", "key_findings", "explanations")

SYSTEM_PROMPT = """You are a senior international trade analyst working for DNEXT, a trade-intelligence company.
You explain ONE anomaly found by comparing the export and import declarations of the same trade flow (UN Comtrade data).

Background:
- The same flow is reported twice: the exporter declares it FOB (Free On Board), the importer declares it CIF (Cost, Insurance, Freight).
- Unit price = trade value / net weight. CIF = FOB + freight + insurance, so the CIF unit price should be slightly above the FOB unit price.
- A flow is flagged when FOB > CIF (import price below export price), or when gap = (CIF - FOB) / FOB exceeds the threshold given in the data.
- Units in the data: values in USD, weights in kg (*_weight_kg), unit prices in USD per kg, gap as a fraction (0.466 = 46.6 %).
  In your text, express unit prices in USD per tonne (multiply by 1000) and weights in tonnes or thousand tonnes
  (94,196,000 kg = 94,196 t = 94.2 thousand tonnes, NOT 94.2 million tonnes).

Typical causes to weigh:
- rerouting / transshipment through third countries (origin or destination recorded differently on each side);
- wars and conflicts (freight and insurance surges, disrupted routes);
- tariffs, sanctions, embargoes (under- or over-invoicing, misdeclared origin or product);
- data-entry errors (unit or weight mistakes, wrong HS code, flows recorded in different periods by each side);
- valuation or product-mix differences inside the same HS code.

Readers: export and import professionals (traders, customs and compliance officers), not data or IT specialists.
- Name countries and the product with reporter_name, partner_name and product_name; never write ISO codes (ARG, CHL...)
  or customs codes in the title.
- Write periods in words ("October 2021", "octobre 2021"), never as 2021-10.
- Trade vocabulary is welcome (FOB, CIF, freight, insurance, customs declaration, invoice, shipment).
  Never mention how the analysis is produced: no "mirror matching", dataset, database, table, field or column names,
  flags, JSON, pipeline, model or artificial intelligence.

Rules:
- Use only the figures provided. Never invent numbers, dates, events or sources.
- Use the evidence: a large mismatch between declared weights points to quantity or recording issues; a one-off gap against a stable history points to a period-specific event; a partner priced very differently from the exporter's other partners narrows the explanation.
- "external_context" lists the external sources available. When it is empty, present geopolitical or policy causes as hypotheses to verify, never as established facts.
- Neutral, factual, concise: one or two sentences per item, the whole report must fit on two pages. Do not accuse anyone of fraud.
- Write every text field in {language}.
- Answer only by calling the submit_report tool."""


def coerce(report):
    """Le modele renvoie parfois un tableau sous forme de chaine (JSON ou balises <item>) : on le decode.
    Il pose aussi parfois les champs d'une explication au premier niveau : on les range dans explanations."""
    if not report.get("explanations"):
        flat = {k: report.pop(k) for k in list(report) if k in EXPLANATION_FIELDS}
        if flat:
            report["explanations"] = [flat]
        elif report.get("item"):
            report["explanations"] = report.pop("item")
    report.pop("item", None)
    for key, spec in REPORT_SCHEMA["properties"].items():
        value = report.get(key)
        if spec["type"] == "array" and isinstance(value, str):
            items = re.findall(r"<item>(.*?)</item>", value, re.S)
            if items and spec["items"]["type"] == "string":
                value = [i.strip() for i in items]
            elif items:
                # <item><category>...</category><hypothesis>...</hypothesis></item>
                value = [{k: v.strip() for k, v in re.findall(r"<(\w+)>(.*?)</\1>", i, re.S)} for i in items]
            else:
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = [value] if spec["items"]["type"] == "string" else None
            report[key] = value
    return report


def problems(report):
    """Ce qui rend le rapport inexploitable, en clair pour le modele ; None si tout va bien.
    Les limites et actions recommandees sont demandees mais facultatives : le PDF s'en passe."""
    missing = [k for k in ESSENTIAL_FIELDS if report.get(k) in (None, "", [])]
    if missing:
        return f"missing or empty fields: {missing}"
    required = REPORT_SCHEMA["properties"]["explanations"]["items"]["required"]
    explanations = report["explanations"]
    if not isinstance(explanations, list) or not all(
        isinstance(e, dict) and all(k in e for k in required) for e in explanations
    ):
        return f"'explanations' must be an array of objects with keys {required}"
    return None


def build_prompt(context):
    return (
        "Here is the flagged flow and its context (JSON):\n<data>\n"
        + json.dumps(context, ensure_ascii=False, indent=1)
        + "\n</data>\nAnalyse it and submit the report."
    )


def converse(messages, language):
    try:
        return bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM_PROMPT.format(language=LANGUAGE_NAMES.get(language, "French"))}],
            messages=messages,
            toolConfig={
                "tools": [{"toolSpec": {
                    "name": "submit_report",
                    "description": "Submit the structured anomaly report. Every field is required.",
                    "inputSchema": {"json": REPORT_SCHEMA},
                }}],
                "toolChoice": {"tool": {"name": "submit_report"}},
            },
            inferenceConfig={"maxTokens": 8000, "temperature": 0.2},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] in RETRYABLE:
            raise BedrockThrottled(str(exc)) from exc
        raise


def handler(event, _context):
    language = event.get("language") or DEFAULT_LANGUAGE
    messages = [{"role": "user", "content": [{"text": build_prompt(event["context"])}]}]
    usage = {"inputTokens": 0, "outputTokens": 0}
    report = {}

    # Si la sortie est incomplete, on renvoie l'erreur au modele dans la meme conversation
    # (une correction coute moins cher qu'une generation complete relancee par Step Functions).
    # Les champs recus sont cumules : la correction ne contient souvent que ce qui manquait.
    for attempt in range(2):
        result = converse(messages, language)
        for key in usage:
            usage[key] += result["usage"][key]
        message = result["output"]["message"]
        tool_uses = [b["toolUse"] for b in message["content"] if "toolUse" in b]
        if not tool_uses:
            raise IncompleteReport(f"Le modele n'a pas appele l'outil (stopReason={result['stopReason']})")

        # Le modele decoupe parfois le rapport en plusieurs appels : on les fusionne
        for tool_use in tool_uses:
            report.update(coerce({k: v for k, v in tool_use["input"].items() if v not in (None, "", [])}))
        problem = problems(report)
        if problem is None:
            return {"report": report, "model_id": MODEL_ID, "usage": usage, "attempts": attempt + 1}

        # Bedrock exige une reponse a chaque appel d'outil du message precedent
        messages += [message, {"role": "user", "content": [{"toolResult": {
            "toolUseId": tool_use["toolUseId"],
            "status": "error",
            "content": [{"text": f"Invalid report: {problem}. Call submit_report again, once, "
                                 "with the complete report."}],
        }} for tool_use in tool_uses]}]

    raise IncompleteReport(f"Rapport toujours invalide apres correction : {problem} "
                           f"(cles recues : {sorted(report)})")
