#!/usr/bin/env python3
"""Construit le zip de la Lambda d'ingestion de façon reproductible.

  * copie le code de src/ingest_lambda/
  * embarque openpyxl (pur Python) ; pandas et pyarrow viennent du layer AWS
  * zip déterministe (dates fixes, ordre trié) -> même code = même hash,
    donc CloudFormation ne redéploie la Lambda que si le code a changé.

Sortie (stdout) : "<chemin_du_zip> <hash12>"
"""
import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "ingest_lambda"
BUILD = ROOT / "build"
PKG = BUILD / "pkg"
ZIP = BUILD / "ingest_lambda.zip"


def main() -> None:
    shutil.rmtree(PKG, ignore_errors=True)
    PKG.mkdir(parents=True)

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
         "--no-compile", "--target", str(PKG), "-r", str(SRC / "requirements.txt")],
        check=True, stdout=sys.stderr,
    )
    for f in SRC.glob("*.py"):
        shutil.copy2(f, PKG / f.name)

    files = sorted(
        p for p in PKG.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            info = zipfile.ZipInfo(p.relative_to(PKG).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, p.read_bytes())

    digest = hashlib.sha256(ZIP.read_bytes()).hexdigest()[:12]
    print(f"{ZIP.as_posix()} {digest}")


if __name__ == "__main__":
    main()
