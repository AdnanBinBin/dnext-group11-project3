# DNEXT – Couche données (Bronze → Silver)

Ce projet crée sur AWS un pipeline qui transforme l'Excel UN Comtrade (`data/Bilateral_data.xlsx`) en une table propre, interrogeable en SQL avec Athena. Tout se crée et se supprime en une commande.

> **Intégration au dépôt commun** : ce dossier est la première étape de la plateforme (voir le [README racine](../README.md)). Ses scripts lisent [`../config.env`](../config.env) (préfixe, région, base Glue) ; depuis la racine, `./scripts/deploy.sh data` le déploie et `./scripts/test.sh data` lance ses tests. Les étapes ci-dessous restent valables pour l'utiliser seul.

## Répliquer chez soi

### 1. Prérequis (WSL, une seule fois)

```bash
sudo apt update && sudo apt install -y unzip python3 python3-pip python3-venv curl
curl -sSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip -q awscliv2.zip && sudo ./aws/install && rm -rf aws awscliv2.zip
```

### 2. Copier le projet dans WSL

Travaille dans ton home WSL, pas dans `/mnt/c` ni dans OneDrive.

```bash
cp -r "/mnt/c/chemin/vers/dnext-data-platform" ~/
cd ~/dnext-data-platform
sed -i 's/\r$//' scripts/*.sh scripts/*.py && chmod +x scripts/*.sh
```

Si l'extraction a créé deux dossiers imbriqués, `ls` affiche un deuxième `dnext-data-platform` au lieu des fichiers du projet : fais alors `cd dnext-data-platform` une fois de plus.

### 3. Identifiants AWS

Utilise tes propres clés IAM.

```bash
aws configure                  # région : eu-north-1, format : json
aws sts get-caller-identity    # vérifie la connexion
```

### 4. Tester, déployer, vérifier

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/        # 25 passed

./scripts/deploy.sh            # crée tout et charge l'Excel (≈ 5 min)
./scripts/smoke_test.sh        # vérifie le résultat dans Athena
```

`deploy.sh` doit se terminer sur `statut : SUCCEEDED` et `lignes Silver : 200,587`, et le smoke test sur `Smoke test OK`.

### 5. Supprimer

```bash
./scripts/destroy.sh           # supprime toutes les ressources
```

`deploy.sh` peut être relancé à tout moment, par exemple après une purge du compte : il détecte et répare ce qui manque.

## Ce qui est créé

Tout est dans la stack CloudFormation **`dnext-trade-data-platform`** (région Stockholm, `eu-north-1`). L'onglet *Resources* liste les 11 ressources.

| Ressource | Nom | Rôle |
|---|---|---|
| Bucket S3 Bronze | `dnext-trade-raw-<compte>` | Excel original, intact et versionné |
| Bucket S3 Silver | `dnext-trade-silver-<compte>` | Données nettoyées en Parquet, lignes rejetées, rapports qualité |
| Bucket S3 Athena | `dnext-trade-athena-<compte>` | Résultats des requêtes Athena (supprimés après 7 jours) |
| Lambda | `dnext-trade-ingest-bronze-to-silver` | Lit l'Excel, valide, nettoie et écrit la Silver |
| Rôle IAM | `…-IngestRole-…` | Permissions de la Lambda : lire Bronze, écrire Silver |
| Permission Lambda | `…-RawBucketInvokePermission-…` | Autorise S3 à déclencher la Lambda |
| Config d'invocation | — | Une seule nouvelle tentative en cas d'erreur |
| Base Glue | `dnext_trade` | Regroupe les tables |
| Table Glue | `trade_flows` | Décrit la table Silver |
| Table Glue | `trade_flows_rejected` | Décrit la table des lignes rejetées |
| Workgroup Athena | `dnext-trade-wg` | Espace de requêtes, plafond de 1 Go lu par requête |

Hors stack, `deploy.sh` crée aussi `dnext-trade-artifacts-<compte>`, qui stocke le code zippé de la Lambda.

**Comment ça s'enchaîne :** un fichier déposé dans le bucket Bronze, sous `comtrade/`, déclenche la Lambda. Elle écrit le résultat dans le bucket Silver. Les tables Glue décrivent ces fichiers, et Athena les interroge.

**Pour faire des requêtes :** console AWS → Athena → *Query editor*. Choisis le workgroup **`dnext-trade-wg`** en haut à droite (pas `primary`), puis la base `dnext_trade`. Des exemples sont dans `sql/silver_queries.sql`.

## La table `trade_flows`

Une ligne correspond à une déclaration Comtrade (un pays déclarant, un mois, un flux, un partenaire, un produit). Ce sont les données de l'Excel, validées, typées et normalisées, sans calcul. Ne jamais additionner les lignes Export et Import : c'est la même expédition déclarée deux fois.

| Colonnes | Contenu |
|---|---|
| `year_month`, `year`, `month`, `period_date` | Période (`year` sert à partitionner : filtrer dessus réduit les données lues) |
| `reporter_iso3`, `flow`, `partner_iso3`, `hs4`, `product` | Les colonnes d'origine, normalisées |
| `value_usd`, `qty_t` | Valeur en USD et tonnage, tels que déclarés |
| `exporter_iso3`, `importer_iso3` | Sens du flux, quel que soit le déclarant |
| `price_basis` | `FOB` pour une déclaration d'export, `CIF` pour une importation |
| `partner_raw`, `partner_is_aggregate` | Code partenaire d'origine, et le marqueur des zones « nes » qui ne sont pas des pays (`OTHER_ASIA`, `EUR_OTHERS`, `_X_`) |
| `dq_flags` | Signalements qualité : `MISSING_QTY`, `ZERO_QTY`, `ZERO_VALUE`, `LOW_QTY` (< 1 t), `PARTNER_CODE_MAPPED`, `PARTNER_AGGREGATE` |
| `source_file`, `source_row`, `ingestion_id`, `ingested_at` | Traçabilité jusqu'à la ligne Excel d'origine |

Les lignes invalides (période, pays ou produit inconnu, valeur négative, doublon…) vont dans `trade_flows_rejected` avec leur motif. Il n'y en a aucune avec le fichier actuel.

## Rôle des fichiers

**Code de la Lambda (`src/ingest_lambda/`)**

| Fichier | Rôle |
|---|---|
| `handler.py` | Point d'entrée de la Lambda : télécharge l'Excel depuis S3, appelle la transformation, écrit les Parquet Silver, les lignes rejetées et le rapport qualité, puis supprime l'ancienne version |
| `transform.py` | Le nettoyage proprement dit (pandas uniquement, sans AWS) : validation de chaque ligne, normalisation, flags qualité, séparation entre lignes valides et rejetées |
| `config.py` | Les référentiels utilisés par la transformation : colonnes attendues, 10 exportateurs, 7 codes HS4, correspondances de codes pays, seuil `LOW_QTY` |

**Scripts (`scripts/`)**

| Fichier | Rôle |
|---|---|
| `build_lambda.py` | Construit le zip de la Lambda (code + openpyxl) |
| `print_report.py` | Affiche joliment le rapport d'ingestion à la fin de `deploy.sh` |
| `deploy.sh` | Crée ou répare toute l'infra, charge l'Excel et attend le résultat |
| `destroy.sh` | Supprime toutes les ressources |
| `smoke_test.sh` | Vérifie la table Silver avec des requêtes Athena |
| `lib.sh` | Fonctions et réglages communs aux scripts (région, noms des ressources…) |

**Autres**

| Fichier | Rôle |
|---|---|
| `infra/data-platform.yaml` | Template CloudFormation décrivant les 11 ressources |
| `tests/test_transform.py` | Tests unitaires de `transform.py`, dont un sur le vrai fichier Excel |
| `sql/silver_queries.sql` | Exemples de requêtes Athena sur la Silver |

## En cas de problème

- **Identifiants invalides :** relance `aws configure`.
- **Ingestion en échec :** `aws logs tail /aws/lambda/dnext-trade-ingest-bronze-to-silver --since 15m`
- **Déploiement en échec :** `aws cloudformation describe-stack-events --stack-name dnext-trade-data-platform`, puis cherche le premier `CREATE_FAILED`.
