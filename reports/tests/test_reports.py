"""Tests locaux de la stack rapports (sans appel AWS).

Lancement depuis la racine du dépôt : ./scripts/test.sh reports
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT.parent / "out"

os.environ.setdefault("AWS_DEFAULT_REGION", "eu-north-1")
os.environ.setdefault("REPORTS_BUCKET", "test-bucket")
os.environ.setdefault("REPORTS_TABLE", "test-table")
os.environ.setdefault("STATE_MACHINE_ARN", "arn:aws:states:eu-north-1:000000000000:stateMachine:test")
os.environ.setdefault("MODEL_ID", "test-model")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")


def load(name):
    # Comme dans la Lambda, les modules voisins de app.py sont importables
    sys.path.insert(0, str(SRC / name))
    try:
        spec = importlib.util.spec_from_file_location(f"{name}_app", SRC / name / "app.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


api = load("api")
collect = load("collect")
narrate = load("narrate")
render = load("render")
SAMPLE = json.loads((ROOT / "events" / "sample_anomaly.json").read_text())


@pytest.mark.parametrize("fob, cif, expected", [
    (400, 440, "OK"),                 # +10 %
    (400, 600, "GAP_ABOVE_35"),       # +50 %
    (400, 380, "CIF_BELOW_FOB"),      # -5 %
    (400, 540, "OK"),                 # +35 % exactement : pas strictement supérieur
])
def test_regle_anomalie_des_slides(fob, cif, expected):
    assert collect.classify(fob, cif, (cif - fob) / fob) == expected


def test_normalize_calcule_prix_unitaires_et_ecart():
    flow = collect.normalize({
        "export_value_usd": "540000000", "export_weight_kg": "2000000000",
        "import_value_usd": 810000000, "import_weight_kg": 2100000000,
    })
    assert flow["fob_unit_price"] == pytest.approx(0.27)
    assert flow["cif_unit_price"] == pytest.approx(0.385714, rel=1e-4)
    assert flow["gap"] == pytest.approx(0.428571, rel=1e-4)
    assert flow["flag"] == "GAP_ABOVE_35"
    assert flow["weight_ratio_import_export"] == pytest.approx(1.05)


def test_normalize_tolere_les_valeurs_manquantes():
    flow = collect.normalize({"export_value_usd": None, "export_weight_kg": 0})
    assert flow["fob_unit_price"] is None and flow["flag"] is None


def test_collect_mode_autonome():
    context = collect.handler({"anomaly": SAMPLE}, None)
    assert context["data_source"] == "request"
    assert context["anomaly"]["flag"] == "GAP_ABOVE_35"
    assert "history" not in context["anomaly"]
    assert [h["period"] for h in context["history"]] == ["2019", "2020", "2021", "2022", "2023", "2024"]
    assert context["history_stats"]["flagged_periods"] == ["2023"]
    assert len(context["peers_same_period"]) == 5


@pytest.mark.parametrize("language, exporter, importer, product, peer", [
    ("fr", "France", "Algérie", "Blé", "Égypte"),
    ("en", "France", "Algeria", "Wheat", "Egypt"),
])
def test_collect_donne_des_noms_lisibles(language, exporter, importer, product, peer):
    context = collect.handler({"anomaly": SAMPLE, "language": language}, None)
    anomaly = context["anomaly"]
    assert (anomaly["reporter_name"], anomaly["partner_name"], anomaly["product_name"]) == (exporter, importer, product)
    assert context["peers_same_period"][1]["partner_name"] == peer
    assert collect.country_name("XYZ", language) == "XYZ"  # code inconnu : affiché tel quel


def test_render_ecrit_les_periodes_en_toutes_lettres():
    fr, en = render.LABELS["fr"], render.LABELS["en"]
    assert render.period_text("2021-10", fr) == "octobre 2021"
    assert render.period_text("2021-10", fr, short=True) == "oct. 21"
    assert render.period_text("2021-10", en) == "October 2021"
    assert render.period_text("2023", fr) == "2023"
    assert render.percent(0.6067, fr, 1, signed=True) == "+60,7 %"
    assert render.percent(-0.05, en, 0, signed=True) == "−5 %"


def gold_row(period, partner="BRA", fob_t=210.0, cif_t=300.0, status="MATCHED", reason="GAP_ABOVE_35"):
    """Ligne mirror_matches telle qu'Athena la renvoie (texte), déjà renommée par GOLD_COLUMNS."""
    gap = cif_t / fob_t - 1 if status == "MATCHED" else None
    return {
        "anomaly_id": "C671CE736AA802F36D74297BE29EE53F", "period": period, "reporter": "ARG",
        "partner": partner, "hs_code": "1001", "product": "Wheat",
        "export_value_usd": str(fob_t * 1000), "export_weight_kg": "1000000",
        "import_value_usd": str(cif_t * 1000), "import_weight_kg": "1000000",
        "fob_unit_price": str(fob_t / 1000), "cif_unit_price": str(cif_t / 1000),
        "gap": None if gap is None else str(gap), "match_status": status, "anomaly_reason": reason,
    }


def test_collect_lit_la_table_gold(monkeypatch):
    calls = []

    def fake_athena(sql, params):
        calls.append(params)
        if "anomaly_id = ?" in sql:
            return [gold_row("2023-06")]
        if "BETWEEN" in sql:
            return [gold_row("2023-05", cif_t=225.0, reason=None), gold_row("2023-06"),
                    gold_row("2023-07", status="MISSING_IMPORT", reason=None)]
        return [gold_row("2023-06"), gold_row("2023-06", partner="CHL", cif_t=230.0, reason=None)]

    monkeypatch.setattr(collect, "ATHENA_DATABASE", "dnext_trade")
    monkeypatch.setattr(collect, "athena_rows", fake_athena)

    context = collect.handler({"anomaly": {"anomaly_id": "c671ce736aa802f36d74297be29ee53f"}}, None)

    assert context["data_source"] == "gold"
    assert calls[1][3:] == ["2022-06", "2024-06"]  # fenêtre de ±12 mois
    anomaly = context["anomaly"]
    assert anomaly["flag"] == "GAP_ABOVE_35" and anomaly["frequency"] == "M"
    assert anomaly["fob_unit_price"] == pytest.approx(0.21)
    assert [h["flag"] for h in context["history"]] == ["OK", "GAP_ABOVE_35", "MISSING_IMPORT"]
    assert context["history_stats"]["flagged_periods"] == ["2023-06"]
    assert context["history_stats"]["periods_without_mirror"] == ["2023-07"]


def test_collect_anomalie_inconnue(monkeypatch):
    monkeypatch.setattr(collect, "ATHENA_DATABASE", "dnext_trade")
    monkeypatch.setattr(collect, "athena_rows", lambda sql, params: [])
    with pytest.raises(collect.AnomalyNotFound):
        collect.handler({"anomaly": {"anomaly_id": "0" * 32}}, None)


def test_shift_month_traverse_les_annees():
    assert collect.shift_month("2023-01", -12) == "2022-01"
    assert collect.shift_month("2023-11", 3) == "2024-02"


def test_api_designation_par_anomaly_id():
    anomaly, key = api.normalize_anomaly({"anomaly_id": "c671ce736aa802f36d74297be29ee53f", "force": True})
    assert key == "ID|C671CE736AA802F36D74297BE29EE53F"
    assert "force" not in anomaly


def test_api_designation_par_chiffres():
    anomaly, key = api.normalize_anomaly({"reporter": "FRA", "partner": "DZA", "hs_code": "1001", "period": "2023"})
    assert key == "FRA|DZA|1001|A|2023"


@pytest.mark.parametrize("body, message", [
    ({"anomaly_id": "pas-un-id"}, "anomaly_id"),
    ({"reporter": "FRA"}, "obligatoires"),
])
def test_api_rejette_les_demandes_invalides(body, message):
    event = {"routeKey": "POST /api/v1/reports", "body": json.dumps(body)}
    result = api.handler(event, None)
    assert result["statusCode"] == 400
    assert message in json.loads(result["body"])["error"]


def test_api_route_inconnue():
    assert api.handler({"routeKey": "GET /api/v1/unknown"}, None)["statusCode"] == 404


def fake_narrative():
    return {
        "model_id": "test-model",
        "usage": {"inputTokens": 1, "outputTokens": 1},
        "report": {
            "title": "Blé France → Algérie 2023 : écart CIF/FOB de 42,9 %",
            "summary": "Résumé de test avec accents : é, è, à, ç, œ, € et « guillemets ».",
            "key_findings": ["Constat 1", "Constat 2"],
            "explanations": [{
                "category": "conflict_disruption", "hypothesis": "Hausse du fret.",
                "likelihood": "medium", "supporting_evidence": "Écart isolé sur 2023.",
                "how_to_verify": "Comparer avec les indices de fret.",
            }],
            "data_limitations": ["Données illustratives"],
            "recommended_actions": ["Vérifier la déclaration algérienne"],
        },
    }


def test_narrate_decode_les_tableaux_renvoyes_en_chaine():
    report = fake_narrative()["report"]
    report["explanations"] = json.dumps(report["explanations"])
    report["key_findings"] = "Un seul constat"
    fixed = narrate.coerce(report)
    assert fixed["explanations"][0]["category"] == "conflict_disruption"
    assert fixed["key_findings"] == ["Un seul constat"]
    assert narrate.problems(fixed) is None


def test_narrate_decode_les_listes_en_balises_item():
    report = fake_narrative()["report"]
    report["key_findings"] = "<item>Poids divergent</item>\n<item>Retour a la normale\nen 2023</item>"
    assert narrate.coerce(report)["key_findings"] == ["Poids divergent", "Retour a la normale\nen 2023"]


def test_narrate_signale_les_rapports_inexploitables():
    report = fake_narrative()["report"]
    report["explanations"] = ["texte libre"]
    assert "explanations" in narrate.problems(report)
    del report["key_findings"]
    assert "key_findings" in narrate.problems(report)


def test_narrate_accepte_un_rapport_sans_actions_ni_limites():
    report = fake_narrative()["report"]
    del report["recommended_actions"], report["data_limitations"]
    assert narrate.problems(report) is None


def test_narrate_lit_les_explications_en_balises():
    report = fake_narrative()["report"]
    expected = report["explanations"]
    report["explanations"] = "".join(
        "<item>" + "".join(f"<{k}>{v}</{k}>" for k, v in e.items()) + "</item>" for e in expected)
    assert narrate.coerce(report)["explanations"] == expected


class FakeBedrock:
    """Renvoie successivement les rapports donnes, comme le ferait l'outil submit_report.
    Une liste de rapports simule un rapport decoupe en plusieurs appels d'outil."""

    def __init__(self, *reports):
        self.reports = list(reports)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs["messages"])
        parts = self.reports.pop(0)
        parts = parts if isinstance(parts, list) else [parts]
        return {
            "stopReason": "tool_use",
            "usage": {"inputTokens": 100, "outputTokens": 50},
            "output": {"message": {"role": "assistant", "content": [
                {"toolUse": {"toolUseId": f"t{len(self.calls)}" + (f"-{i}" if i else ""),
                             "name": "submit_report", "input": part}}
                for i, part in enumerate(parts)
            ]}},
        }


def test_narrate_fusionne_un_rapport_decoupe_en_plusieurs_appels(monkeypatch):
    complete = fake_narrative()["report"]
    first = {k: v for k, v in complete.items() if k not in ("explanations", "recommended_actions")}
    second = {"explanations": complete["explanations"], "recommended_actions": complete["recommended_actions"]}
    monkeypatch.setattr(narrate, "bedrock", FakeBedrock([first, second]))

    result = narrate.handler({"context": {}, "language": "fr"}, None)

    assert result["attempts"] == 1 and result["report"] == complete


def test_narrate_range_une_explication_posee_au_premier_niveau():
    complete = fake_narrative()["report"]
    flat = {k: v for k, v in complete.items() if k != "explanations"}
    flat.update(complete["explanations"][0])
    assert narrate.coerce(flat) == complete


def test_narrate_cumule_les_champs_de_la_correction(monkeypatch):
    complete = fake_narrative()["report"]
    first = {k: v for k, v in complete.items() if k != "summary"}
    fake = FakeBedrock(first, {"summary": complete["summary"]})
    monkeypatch.setattr(narrate, "bedrock", fake)

    result = narrate.handler({"context": {}, "language": "fr"}, None)

    assert result["attempts"] == 2 and result["report"] == complete


def test_narrate_repond_a_chaque_appel_d_outil_lors_de_la_correction(monkeypatch):
    complete = fake_narrative()["report"]
    fake = FakeBedrock([{"title": "x"}, {"summary": "y"}], complete)
    monkeypatch.setattr(narrate, "bedrock", fake)

    narrate.handler({"context": {}, "language": "fr"}, None)

    answered = [b["toolResult"]["toolUseId"] for b in fake.calls[1][-1]["content"]]
    assert answered == ["t1", "t1-1"]


def test_narrate_fait_corriger_une_sortie_incomplete(monkeypatch):
    complete = fake_narrative()["report"]
    incomplete = {k: v for k, v in complete.items() if k != "explanations"}
    fake = FakeBedrock(incomplete, complete)
    monkeypatch.setattr(narrate, "bedrock", fake)

    result = narrate.handler({"context": {"anomaly": {}}, "language": "fr"}, None)

    assert result["attempts"] == 2
    assert result["usage"] == {"inputTokens": 200, "outputTokens": 100}
    retry_message = fake.calls[1][-1]["content"][0]["toolResult"]
    assert retry_message["status"] == "error" and retry_message["toolUseId"] == "t1"


def test_narrate_abandonne_apres_une_correction(monkeypatch):
    incomplete = {"title": "x"}
    monkeypatch.setattr(narrate, "bedrock", FakeBedrock(incomplete, dict(incomplete)))
    with pytest.raises(narrate.IncompleteReport):
        narrate.handler({"context": {}, "language": "fr"}, None)


@pytest.mark.parametrize("language", ["fr", "en"])
def test_render_produit_un_pdf(language, tmp_path):
    state = {
        "report_id": "00000000-test",
        "language": language,
        "context": collect.handler({"anomaly": SAMPLE, "language": language}, None),
        "narrative": fake_narrative(),
    }
    pdf = render.build_pdf(state, render.LABELS[language])
    assert pdf.startswith(b"%PDF")
    (tmp_path / f"report-{language}.pdf").write_bytes(pdf)
    OUT.mkdir(exist_ok=True)
    (OUT / f"test-render-{language}.pdf").write_bytes(pdf)


def test_render_rapport_gold_mensuel(monkeypatch):
    monkeypatch.setattr(collect, "ATHENA_DATABASE", "dnext_trade")
    months = [collect.shift_month("2022-06", i) for i in range(25)]
    monkeypatch.setattr(collect, "athena_rows", lambda sql, params: (
        [gold_row("2023-06")] if "anomaly_id = ?" in sql else
        [gold_row(m, cif_t=300.0 if m == "2023-06" else 225.0,
                  reason="GAP_ABOVE_35" if m == "2023-06" else None) for m in months]
        if "BETWEEN" in sql else [gold_row("2023-06")]))
    state = {
        "report_id": "00000000-gold",
        "language": "fr",
        "context": collect.handler({"anomaly": {"anomaly_id": "C671CE736AA802F36D74297BE29EE53F"}}, None),
        "narrative": fake_narrative(),
    }
    pdf = render.build_pdf(state, render.LABELS["fr"])
    OUT.mkdir(exist_ok=True)
    (OUT / "test-render-gold.pdf").write_bytes(pdf)
    assert pdf.startswith(b"%PDF")


def test_render_sans_historique():
    anomaly = {k: v for k, v in SAMPLE.items() if k not in ("history", "peers")}
    state = {
        "report_id": "00000000-test",
        "language": "fr",
        "context": collect.handler({"anomaly": anomaly}, None),
        "narrative": fake_narrative(),
    }
    assert render.build_pdf(state, render.LABELS["fr"]).startswith(b"%PDF")
