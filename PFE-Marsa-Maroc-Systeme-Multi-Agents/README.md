# Marsa Maroc — Plateforme web IA de Codification des stocks

Application web (architecture frontend / backend) pour la migration et la
codification automatique des articles de stock : **export SAP/Apipro → SAP S/4HANA**.

## Architecture

```
┌──────────────────────────┐     HTTP/JSON      ┌─────────────────────────┐
│   FRONTEND (navigateur)  │  ───────────────►  │   BACKEND  (Flask API)  │
│  HTML · Tailwind · JS    │  ◄───────────────  │   main.py               │
│  Chart.js · agent rail   │                    │                         │
└──────────────────────────┘                    │   ┌───────────────────┐ │
                                                 │   │  PIPELINE 5 AGENTS│ │
                                                 │   │ marsa_pipeline.py │ │
                                                 │   └───────────────────┘ │
                                                 │            │            │
                                                 │   ┌────────▼──────────┐ │
                                                 │   │  MongoDB (option) │ │
                                                 │   │  marsa_mongo.py   │ │
                                                 │   └───────────────────┘ │
                                                 └─────────────────────────┘
```

| Couche | Fichier | Rôle |
|--------|---------|------|
| Frontend | `frontend/index.html · app.js · styles.css` | Upload, rail d'agents animé, tableau de bord, files de validation, export SAP |
| API | `backend/main.py` | Endpoints REST, exécution asynchrone du pipeline, génération Excel |
| Pipeline | `backend/marsa_pipeline.py` | Les 5 agents + l'orchestrateur |
| Staging | `backend/marsa_mongo.py` | Persistance MongoDB **optionnelle** |

## Les agents
1. **Nettoyage** — casse, prestations vs articles, score qualité
2. **Déduplication** — TF-IDF + plus proches voisins cosinus (scalable 50k+)
3. **Normalisation** — convention SAP, description ≤ 40 caractères
4. **Classification** — weak supervision (règles → classifieur), 22 familles, confiance
5. **Enrichissement** — extraction marque / référence / dimensions du texte libre

> Agent 6 (optimisation stock) hors périmètre : l'export `ARTICLE_SAP` ne
> contient pas de données de stock/prix.

## Lancer en local
```bash
cd marsa-web
pip install -r requirements.txt
python backend/main.py
```
Puis ouvrir **http://localhost:5000** et déposer un fichier `.xlsx`.

## Endpoints API
| Méthode | Route | Rôle |
|---------|-------|------|
| POST | `/api/upload` | Charge un .xlsx, retourne aperçu + `job_id` |
| POST | `/api/run/<job_id>` | Lance le pipeline (asynchrone) |
| GET  | `/api/status/<job_id>` | État d'avancement (pour le rail) |
| GET  | `/api/results/<job_id>/dashboard` | KPIs + données graphiques |
| GET  | `/api/results/<job_id>/validation` | File human-in-the-loop |
| GET  | `/api/results/<job_id>/duplicates` | Doublons détectés |
| GET  | `/api/results/<job_id>/sap` | Fiches prêtes BAPI |
| GET  | `/api/results/<job_id>/download/<kind>` | Export Excel (validation/sap/full) |
| POST | `/api/mongo/test` · `/api/mongo/save/<job_id>` | Staging optionnel |

## MongoDB (optionnel)
Cliquer sur **Staging ●** en haut à droite, coller l'URI, tester, sauvegarder.
L'application fonctionne parfaitement **sans** Mongo.
