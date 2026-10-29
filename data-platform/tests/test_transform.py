"""Tests unitaires de la transformation Bronze -> Silver.

Lancement :  python -m pytest tests/   (ou  python -m unittest discover tests)
Ne nécessite que pandas + openpyxl (pas d'AWS).
"""
import os
import sys
import unittest
from datetime import date, datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "ingest_lambda"))

from transform import SILVER_COLUMNS, SchemaError, read_source, transform  # noqa: E402

DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "Bilateral_data.xlsx")
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def make_raw(rows):
    cols = ["YearMonth", "Reporter", "Flow", "Partner", "HS4", "Product", "Value_USD", "Qty_t"]
    return pd.DataFrame([[None if v is None else str(v) for v in r] for r in rows], columns=cols)


def run(rows):
    return transform(make_raw(rows), ingestion_id="test", source_file="s3://raw/test.xlsx",
                     ingested_at=NOW)


GOOD_EXPORT = ["2023-05", "FRA", "Export", "EGY", "1001", "Wheat", 400000, 1000]
GOOD_IMPORT = ["2023-05", "EGY", "Import", "FRA", "1001", "Wheat", 440000, 1000]


class TestHappyPath(unittest.TestCase):
    def test_both_declarations_kept_and_oriented(self):
        r = run([GOOD_EXPORT, GOOD_IMPORT])
        self.assertEqual(len(r.silver), 2)
        self.assertEqual(len(r.rejected), 0)
        exp = r.silver[r.silver.flow == "Export"].iloc[0]
        imp = r.silver[r.silver.flow == "Import"].iloc[0]
        # Les deux déclarations décrivent le même flux physique FRA -> EGY
        for row in (exp, imp):
            self.assertEqual(row.exporter_iso3, "FRA")
            self.assertEqual(row.importer_iso3, "EGY")
        self.assertEqual(exp.price_basis, "FOB")
        self.assertEqual(imp.price_basis, "CIF")
        # Les valeurs source sont conservées telles quelles (aucun calcul métier)
        self.assertEqual(exp.value_usd, 400000)
        self.assertEqual(imp.value_usd, 440000)
        self.assertNotIn("unit_price_usd_per_t", r.silver.columns)

    def test_types_and_columns(self):
        r = run([GOOD_EXPORT])
        self.assertEqual(list(r.silver.columns), SILVER_COLUMNS)
        row = r.silver.iloc[0]
        self.assertEqual(row.year, 2023)
        self.assertEqual(row.month, 5)
        self.assertEqual(row.period_date, date(2023, 5, 1))
        self.assertEqual(str(r.silver["ingested_at"].dt.tz), "UTC")
        self.assertEqual(row.source_row, 2)

    def test_normalisation(self):
        r = run([["2023-05", " fra ", "export", "egy", "1001.0", "wheat", "400000", "1000"]])
        row = r.silver.iloc[0]
        self.assertEqual((row.reporter_iso3, row.flow, row.partner_iso3, row.hs4, row["product"]),
                         ("FRA", "Export", "EGY", "1001", "Wheat"))
        self.assertIn("PRODUCT_LABEL_FIXED", row.dq_flags)


class TestPartners(unittest.TestCase):
    def test_partner_label_mapped_to_iso3(self):
        r = run([["2023-05", "RUS", "Export", "ALBA", "1001", "Wheat", 1000, 5]])
        row = r.silver.iloc[0]
        self.assertEqual(row.partner_iso3, "ALB")
        self.assertEqual(row.partner_raw, "ALBA")
        self.assertIn("PARTNER_CODE_MAPPED", row.dq_flags)

    def test_aggregate_partner_kept_and_flagged(self):
        r = run([["2023-05", "USA", "Export", "OTHER_ASIA", "1201", "Soybeans", 1000, 5],
                 ["2023-05", "ARG", "Export", "_X_", "1001", "Wheat", 1000, 5]])
        self.assertEqual(len(r.silver), 2)
        self.assertTrue(r.silver.partner_is_aggregate.all())

    def test_unknown_partner_rejected(self):
        r = run([["2023-05", "USA", "Export", "WHATEVER", "1001", "Wheat", 1000, 5]])
        self.assertEqual(r.rejected.reject_reason.tolist(), ["INVALID_PARTNER"])


class TestRejections(unittest.TestCase):
    def assertRejected(self, row, reason):
        r = run([row])
        self.assertEqual(len(r.silver), 0, reason)
        self.assertEqual(r.rejected.reject_reason.iloc[0], reason)
        self.assertEqual(r.report["rejected_by_reason"], {reason: 1})

    def test_bad_period(self):
        self.assertRejected(["2023-13", "FRA", "Export", "EGY", "1001", "Wheat", 1, 1], "INVALID_PERIOD")
        self.assertRejected(["05/2023", "FRA", "Export", "EGY", "1001", "Wheat", 1, 1], "INVALID_PERIOD")
        self.assertRejected(["2018-12", "FRA", "Export", "EGY", "1001", "Wheat", 1, 1], "INVALID_PERIOD")

    def test_future_period(self):
        self.assertRejected(["2027-01", "FRA", "Export", "EGY", "1001", "Wheat", 1, 1], "INVALID_PERIOD")

    def test_bad_flow(self):
        self.assertRejected(["2023-05", "FRA", "Re-export", "EGY", "1001", "Wheat", 1, 1], "INVALID_FLOW")

    def test_bad_reporter(self):
        self.assertRejected(["2023-05", "FR", "Export", "EGY", "1001", "Wheat", 1, 1], "INVALID_REPORTER")

    def test_self_trade(self):
        self.assertRejected(["2023-05", "FRA", "Export", "FRA", "1001", "Wheat", 1, 1], "SELF_TRADE")

    def test_unknown_hs4(self):
        self.assertRejected(["2023-05", "FRA", "Export", "EGY", "9999", "Gold", 1, 1], "UNKNOWN_HS4")

    def test_out_of_scope(self):
        # Ni l'exportateur déclarant (DEU) ni le partenaire ne sont dans les 10 exportateurs
        self.assertRejected(["2023-05", "DEU", "Export", "EGY", "1001", "Wheat", 1, 1], "OUT_OF_SCOPE")
        self.assertRejected(["2023-05", "EGY", "Import", "DEU", "1001", "Wheat", 1, 1], "OUT_OF_SCOPE")

    def test_bad_value(self):
        self.assertRejected(["2023-05", "FRA", "Export", "EGY", "1001", "Wheat", None, 1], "INVALID_VALUE")
        self.assertRejected(["2023-05", "FRA", "Export", "EGY", "1001", "Wheat", -5, 1], "INVALID_VALUE")
        self.assertRejected(["2023-05", "FRA", "Export", "EGY", "1001", "Wheat", "abc", 1], "INVALID_VALUE")

    def test_negative_qty(self):
        self.assertRejected(["2023-05", "FRA", "Export", "EGY", "1001", "Wheat", 10, -1], "INVALID_QTY")

    def test_duplicate_key_keeps_first(self):
        r = run([GOOD_EXPORT, GOOD_EXPORT[:6] + [999, 1]])
        self.assertEqual(len(r.silver), 1)
        self.assertEqual(r.silver.iloc[0].value_usd, 400000)
        self.assertEqual(r.rejected.reject_reason.tolist(), ["DUPLICATE_KEY"])


class TestQualityFlags(unittest.TestCase):
    def test_missing_and_zero_qty_kept_and_flagged(self):
        r = run([["2023-05", "FRA", "Export", "EGY", "1001", "Wheat", 100, None],
                 ["2023-05", "FRA", "Export", "MAR", "1001", "Wheat", 100, 0]])
        self.assertEqual(len(r.silver), 2)
        self.assertTrue(r.silver.qty_t.isna().iloc[0] or r.silver.qty_t.isna().iloc[1])
        self.assertEqual(sorted(r.silver.dq_flags), ["MISSING_QTY", "ZERO_QTY"])

    def test_low_qty_flag(self):
        r = run([["2023-05", "ROU", "Export", "EGY", "1001", "Wheat", 11, 0.021]])
        row = r.silver.iloc[0]
        self.assertIn("LOW_QTY", row.dq_flags)
        self.assertAlmostEqual(row.qty_t, 0.021)

    def test_clean_row_has_no_flag(self):
        self.assertIsNone(run([GOOD_EXPORT]).silver.iloc[0].dq_flags)


class TestSchema(unittest.TestCase):
    def test_missing_column(self):
        with self.assertRaises(SchemaError):
            transform(make_raw([GOOD_EXPORT]).drop(columns=["Qty_t"]),
                      ingestion_id="t", source_file="s")

    def test_empty_file(self):
        with self.assertRaises(SchemaError):
            transform(make_raw([]), ingestion_id="t", source_file="s")


@unittest.skipUnless(os.path.exists(DATA_FILE), "fichier de données absent")
class TestRealDataset(unittest.TestCase):
    """Test de non-régression sur le vrai fichier fourni par DNEXT."""

    @classmethod
    def setUpClass(cls):
        cls.r = transform(read_source(DATA_FILE), ingestion_id="real",
                          source_file="s3://raw/Bilateral_data.xlsx", ingested_at=NOW)

    def test_no_row_lost(self):
        rep = self.r.report
        self.assertEqual(rep["rows_in"], 200_587)
        self.assertEqual(rep["rows_silver"] + rep["rows_rejected"], rep["rows_in"])

    def test_scope(self):
        self.assertEqual(set(self.r.report["exporters"]),
                         {"USA", "BRA", "ARG", "CAN", "AUS", "RUS", "UKR", "FRA", "KAZ", "ROU"})
        self.assertEqual(self.r.report["period_min"], "2019-01")
        self.assertEqual(self.r.report["period_max"], "2026-08")

    def test_keys_unique(self):
        key = ["year_month", "reporter_iso3", "flow", "partner_iso3", "hs4"]
        self.assertFalse(self.r.silver.duplicated(key).any())

    def test_russia_has_no_exports_after_2021(self):
        s = self.r.silver
        rus_exp = s[(s.reporter_iso3 == "RUS") & (s.flow == "Export")]
        self.assertLessEqual(rus_exp.year_month.max(), "2021-12")
        # ...mais reste visible via les importations de ses partenaires
        self.assertTrue(((s.exporter_iso3 == "RUS") & (s.year >= 2022)).any())


if __name__ == "__main__":
    unittest.main()
