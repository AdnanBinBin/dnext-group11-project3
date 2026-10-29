#!/usr/bin/env python3
"""Affiche le rapport d'ingestion (JSON sur stdin). Code retour 1 si l'ingestion a échoué."""
import json
import sys

r = json.load(sys.stdin)
print()
print(f"  statut          : {r['status']}")
print(f"  ingestion_id    : {r['ingestion_id']}")
if r["status"] != "SUCCEEDED":
    print(f"  erreur          : {r.get('error')}\n")
    sys.exit(1)
print(f"  lignes source   : {r['rows_in']:,}")
print(f"  lignes Silver   : {r['rows_silver']:,}")
print(f"  lignes rejetées : {r['rows_rejected']:,}  {r['rejected_by_reason']}")
print(f"  période         : {r['period_min']} -> {r['period_max']}")
print("  flags qualité   : " + ", ".join(f"{k}={v}" for k, v in r["flags_on_silver_rows"].items() if v))
print()
