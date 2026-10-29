# DNEXT — Cloud-Native Trade Anomaly Detection & Reporting Platform

Projet 3 de TSM_CloudSys (HES-SO, 2026-2027), groupe 11 : Adnan Binjos, Théo Falcinelli, Alexandre Schmid.

La plateforme ingère les déclarations UN Comtrade, rapproche les déclarations d'export (FOB) et
d'import (CIF) d'un même flux (*mirror matching*), signale les anomalies, les expose dans une
heatmap et rédige à la demande un rapport PDF expliquant une anomalie avec Amazon Bedrock.

Tout est serverless, sur AWS en **`eu-north-1`** (Stockholm), décrit en CloudFormation et
déployable en une commande.


| Composant | Dossier | Stack(s) | Responsable |
|---|---|---|---|
| Bronze → Silver | [`data-platform/`](data-platform/README.md) | `dnext-trade-data-platform` | Théo |
| Gold + API de consultation | [`backend/`](backend/README.md) | `dnext-trade-gold`, `dnext-trade-backend` | Alexandre |
| Rapports IA | [`reports/`](reports/README.md) | `dnext-trade-reports` | Adnan |
| Frontend de démo | [`frontend/`](frontend/) | `dnext-trade-frontend` (Amplify) | Adnan |

Les détails de chaque composant (choix, schémas, limites) sont dans leur README respectifs.

## Conventions communes

- **Configuration unique** : [`config.env`](config.env) (préfixe `dnext-trade`, région, base Glue,
  workgroup Athena, modèle Bedrock). Chaque valeur peut être surchargée par une variable d'environnement.
- **Nommage** : toutes les ressources commencent par `dnext-trade-`, toutes les stacks portent les tags
  `project=dnext-trade`, `course=T-CloudSys`, `group=gr11`.
- **Une seule API** : la stack `reports` ajoute ses routes à l'API HTTP du backend ;
  le frontend n'a qu'une URL de base.
- **Vocabulaire des anomalies** : celui de la Gold, partout : `CIF_BELOW_FOB`, `GAP_ABOVE_35`
  (écart = CIF / FOB − 1, seuil strict de 35 %), statuts `MATCHED`, `MISSING_EXPORT`, `MISSING_IMPORT`,
  `INVALID_PRICE`, `AGGREGATE_PARTNER`.
- **Arborescence d'un composant** : `infra/` (CloudFormation), `src/` (code des Lambdas),
  `scripts/` (déploiement), `tests/`, `sql/` le cas échéant.

## Démarrage

Prérequis (WSL / Linux) : AWS CLI v2 configurée, Python 3, [uv](https://docs.astral.sh/uv/), `curl`.
Les scripts se lancent depuis bash : dans PowerShell, `./scripts/deploy.sh` ne fait rien ; passer par
`wsl -d Ubuntu -- bash -lc "cd ~/TSM_Cloud/projet3-gr11 && ./scripts/deploy.sh"`.
Le build de la Lambda d'ingestion utilise `pip` : lancer `data` depuis un environnement virtuel
(`python3 -m venv .venv && source .venv/bin/activate`).

```bash
./scripts/test.sh                  # tests unitaires des trois composants, sans AWS
./scripts/deploy.sh                # tout : data → gold → api → reports → frontend (≈ 10 min la première fois)
./scripts/deploy.sh reports        # ou un seul composant
./scripts/status.sh                # stacks, buckets, tables, santé de l'API, URL du frontend
frontend/scripts/serve.sh          # frontend Vue en local (Vite) sur http://localhost:8080
reports/scripts/test_report.sh     # rapport IA de bout en bout sur une vraie anomalie
```

Le frontend est une application **Vue 3** (Vite, composants dans `frontend/src/`). Il est publié sur
Amplify Hosting sans dépôt Git relié : `frontend/scripts/deploy.sh` construit l'application
(`npm ci && npm run build`, Node.js 20 ou plus) et envoie un zip du build avec un `config.js` qui
porte l'URL de l'API. Si la stack de l'API
est recréée, son URL change : redéployer aussi `frontend`.

Chaque étape est idempotente : après une purge du compte, `./scripts/deploy.sh` recrée ce qui manque.
Avant chaque déploiement, une stack en échec, ou dont des ressources ont été supprimées hors
CloudFormation (détection de dérive), est supprimée puis recréée entièrement ; le bucket Gold,
en `Retain`, garde ses données.
La Silver n'est réingérée que si elle est vide, et la Gold n'est calculée que si sa table est vide
(`backend/scripts/build_gold.sh --rebuild` pour la recalculer).

### Arrêter

```bash
./scripts/destroy.sh frontend reports api   # plus rien d'exposé, les données restent
./scripts/deploy.sh api reports frontend    # le lendemain : tout revient en ~5 min
./scripts/destroy.sh all                    # tout, données comprises (confirmation demandée)
```

Rien n'est planifié : ces scripts ne s'exécutent que lorsqu'on les lance.

## Arborescence

```
config.env            configuration commune
scripts/              deploy, destroy, status, test (orchestration de tous les composants)
data-platform/        Bronze → Silver (Excel Comtrade, Lambda d'ingestion, Glue, Athena)
backend/              Gold (SQL Athena) + API HTTP de consultation (openapi.yaml)
reports/              workflow de rapports IA (Step Functions, Bedrock, PDF)
frontend/             application Vue 3 : filtres, heatmap, anomalies, rapports (Amplify Hosting)
```
