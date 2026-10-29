# DNEXT — Rapports IA sur une anomalie

Un clic sur une anomalie de la heatmap déclenche un workflow asynchrone qui rassemble le
contexte chiffré dans la table Gold, fait rédiger une explication par Claude (Amazon Bedrock)
et produit un rapport PDF stocké dans S3. Ce composant fait partie du dépôt global :
voir le [README racine](../README.md) pour l'architecture et le déploiement complet.

```
infra/template.yaml   infrastructure (routes API, Step Functions, Lambdas, DynamoDB, S3, IAM), transform SAM
src/api               routes /api/v1/reports : demande, statut, liste, téléchargement
src/collect           étape 1 : anomalie, historique ±12 mois et pays comparables (Athena, table Gold)
src/narrate           étape 2 : rédaction structurée par Bedrock (Claude Haiku 4.5, profil eu.)
src/render            étape 3 : graphiques matplotlib + PDF fpdf2 → S3
layer/                dépendances de render (couche Lambda construite par scripts/deploy.sh)
events/               anomalie d'exemple pour le mode autonome (données illustratives des slides)
scripts/              deploy.sh, destroy.sh, test_report.sh
tests/                tests unitaires (sans AWS)
```

## Utilisation

```bash
./scripts/deploy.sh reports                              # depuis la racine ; nécessite la stack backend
reports/scripts/test_report.sh                           # rapport sur la 1re anomalie ARG / blé, PDF dans out/
reports/scripts/test_report.sh --id C671CE736AA802F36D74297BE29EE53F --force
reports/scripts/test_report.sh --file reports/events/sample_anomaly.json   # mode autonome
./scripts/test.sh reports                                # tests unitaires
./scripts/destroy.sh reports
```

La couche Python est construite avec `uv` à partir de wheels Linux x86_64 / Python 3.13, quelle
que soit la version locale. Pas besoin de SAM CLI ni de Docker : `aws cloudformation package`
envoie le code dans S3 et AWS applique lui-même la transform SAM.

## Workflow de génération

```
POST /api/v1/reports ──► DynamoDB (PENDING) ──► Step Functions dnext-trade-report-workflow
                                                  MarkRunning     DynamoDB → RUNNING
                                                  CollectContext  Lambda + Athena (mirror_matches)
                                                  WriteNarrative  Lambda + Bedrock (outil imposé → JSON)
                                                  RenderPdf       Lambda → S3 reports/<id>/report.pdf|json
                                                  MarkCompleted   DynamoDB → COMPLETED
                                                  (toute erreur)  MarkFailed → FAILED + cause
```

- **Asynchrone** : l'API répond `202` immédiatement, le front interroge le statut.
- **Sortie du modèle incomplète** : la Lambda renvoie l'erreur au modèle dans la même
  conversation pour qu'il corrige (un seul appel supplémentaire).
- **Tentatives gérées par Step Functions** : saturation Bedrock (`BedrockThrottled`, 3 essais
  espacés), rapport toujours inexploitable (`IncompleteReport`, 1 essai), erreurs Lambda.
- **Pas de chiffres inventés** : le modèle ne produit que le texte ; chiffres, graphiques et
  mise en page sont du code déterministe à partir de la Gold.
- **Réutilisation** : un second clic sur la même anomalie renvoie le rapport existant (pas de
  nouvel appel Bedrock) ; `"force": true` en génère un nouveau.

## API

Routes ajoutées à l'API HTTP commune de la stack `dnext-trade-backend` (même URL de base que
`/api/v1/heatmap`, mêmes règles CORS et de débit). Erreurs au format `{"error": "..."}`.

| Méthode | Chemin | Rôle |
|---|---|---|
| `POST` | `/api/v1/reports` | demande un rapport → `202 {report_id, status, status_url}` ou `200` si déjà existant (`reused: true`) |
| `GET` | `/api/v1/reports/{id}` | statut (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`), `title`, `pdf_url` / `json_url` présignées (1 h) |
| `GET` | `/api/v1/reports/{id}/pdf` | redirection vers le PDF |
| `GET` | `/api/v1/reports?anomaly_id=` | rapports déjà générés pour une anomalie |

Corps du `POST`, avec l'identifiant renvoyé par `GET /api/v1/anomalies` :

```json
{"anomaly_id": "C671CE736AA802F36D74297BE29EE53F"}
```

Options : `"language": "en"` (défaut `fr`), `"force": true`.

**Mode autonome** (tests, démo sans données) : à la place de `anomaly_id`, envoyer les chiffres
du flux (`reporter`, `partner`, `hs_code`, `period`, `export_value_usd`, `export_weight_kg`,
`import_value_usd`, `import_weight_kg`, et optionnellement `history`, `peers`).

## Lecture de la Gold

`collect` lit `dnext_trade.mirror_matches` (colonnes décrites dans
[`backend/README.md`](../backend/README.md)) avec trois requêtes Athena paramétrées :
l'anomalie par `anomaly_id`, l'historique du même couple exportateur/importateur/produit sur
±12 mois, et les 15 principaux partenaires du même exportateur sur le même mois. Les colonnes
sont converties dans les unités internes : poids en kg, prix en USD/kg, écart en fraction.
Les motifs d'anomalie gardent le vocabulaire Gold (`CIF_BELOW_FOB`, `GAP_ABOVE_35`).

## Sécurité et coûts

- Un rôle IAM par Lambda et pour la machine à états, limité à ses ressources : lecture de la
  table Gold et du workgroup Athena, appel du seul profil Bedrock configuré.
- Bucket des rapports privé et chiffré ; PDF servis par URL présignée (1 h).
- Coût d'un rapport : un appel Claude Haiku 4.5 (~4 000 tokens en entrée, ~2 400 en sortie), trois
  petites requêtes Athena et quelques secondes de Lambda, soit environ 2 centimes. Durée : 30 à 80 s.

## Reste à faire

- Contexte externe (`external_context`, vide pour l'instant) : tarifs douaniers, listes de
  sanctions, actualités. Le modèle présente les causes géopolitiques comme des hypothèses à vérifier.
- Pays affichés en codes ISO3 dans le PDF (noms complets à ajouter).
