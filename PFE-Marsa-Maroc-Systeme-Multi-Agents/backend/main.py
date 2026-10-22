# ═══════════════════════════════════════════════════════════════════════
# MARSA MAROC — Backend API (Flask)
# Auth (login partagé) · Pipeline 5 agents · Destination MongoDB
# ═══════════════════════════════════════════════════════════════════════

import io
import os
import time
import uuid
import threading
import functools
import pandas as pd
from flask import (Flask, request, jsonify, send_file, send_from_directory,
                   session, redirect)

from marsa_pipeline import charger_export_sap, OrchestrateurMarsa
import marsa_mongo

app = Flask(__name__, static_folder="../frontend", static_url_path="")
app.secret_key = os.environ.get("MARSA_SECRET", "marsa-maroc-secret-change-me")

# ── Identifiants partagés (configurables par variables d'environnement) ──
AUTH_USER = os.environ.get("MARSA_USER", "admin")
AUTH_PASS = os.environ.get("MARSA_PASS", "marsa2026")

# ── Config MongoDB (local par défaut) ──
MONGO_URI = os.environ.get("MONGO_URI", marsa_mongo.URI_DEFAUT)

# Store en mémoire : { job_id : {...} }
JOBS = {}


# ═══════════════════════════════════════════════════════════ AUTH
def require_auth(f):
    @functools.wraps(f)
    def wrapper(*a, **k):
        if not session.get("auth"):
            return jsonify({"error": "non_authentifie"}), 401
        return f(*a, **k)
    return wrapper


@app.route("/api/login", methods=["POST"])
def login():
    data = request.json or {}
    user = data.get("username", data.get("user", ""))
    pwd = data.get("password", data.get("pass", ""))
    if user == AUTH_USER and pwd == AUTH_PASS:
        session["auth"] = True
        session["user"] = AUTH_USER
        return jsonify({"ok": True, "user": AUTH_USER})
    return jsonify({"ok": False, "error": "Identifiants incorrects"}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    return jsonify({"auth": bool(session.get("auth")), "user": session.get("user")})


# ═══════════════════════════════════════════════════════════ STATIC
@app.route("/")
def index():
    # Le gate de connexion est géré côté frontend via /api/me.
    return send_from_directory(app.static_folder, "index.html")


# ═══════════════════════════════════════════════════════════ UPLOAD
@app.route("/api/upload", methods=["POST"])
@require_auth
def upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "Aucun fichier reçu"}), 400
    try:
        df_brut = charger_export_sap(io.BytesIO(f.read()))
    except Exception as e:
        return jsonify({"error": f"Lecture impossible : {e}"}), 400
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"df_brut": df_brut, "df_res": None, "rapport": None,
                    "statut": "pret", "nom": f.filename, "progress": []}
    preview = df_brut.head(8)[["code_sap", "description"]].to_dict("records")
    return jsonify({"job_id": job_id, "nom": f.filename,
                    "n_articles": int(len(df_brut)), "preview": preview})


# ═══════════════════════════════════════════════════════════ RUN
def _executer_job(job_id):
    job = JOBS[job_id]
    job["statut"] = "en_cours"
    MAP = {"[Agent 1]": "Nettoyage", "[Agent 2]": "Déduplication",
           "[Agent 3]": "Normalisation",
           "[Agent 4] Classification taxonomique": "Classification",
           "[Agent 5]": "Enrichissement"}

    class OrchTrace(OrchestrateurMarsa):
        def _log(self, *a):
            txt = " ".join(str(x) for x in a)
            for cle, station in MAP.items():
                if cle in txt and station not in job["progress"]:
                    job["progress"].append(station)

    import contextlib
    t0 = time.time()
    orch = OrchTrace(verbose=True)
    with contextlib.redirect_stdout(io.StringIO()):
        df = orch.executer(job["df_brut"])
    orch.rapport["duree_s"] = round(time.time() - t0, 1)
    job["df_res"] = df
    job["rapport"] = orch.rapport
    job["statut"] = "termine"
    job["progress"] = ["Nettoyage", "Déduplication", "Normalisation",
                       "Classification", "Enrichissement"]


@app.route("/api/run/<job_id>", methods=["POST"])
@require_auth
def run(job_id):
    if job_id not in JOBS:
        return jsonify({"error": "Job introuvable"}), 404
    threading.Thread(target=_executer_job, args=(job_id,), daemon=True).start()
    return jsonify({"job_id": job_id, "statut": "lance"})


@app.route("/api/status/<job_id>")
@require_auth
def status(job_id):
    if job_id not in JOBS:
        return jsonify({"error": "Job introuvable"}), 404
    j = JOBS[job_id]
    return jsonify({"statut": j["statut"], "progress": j["progress"],
                    "termine": j["statut"] == "termine"})


# ═══════════════════════════════════════════════════════════ RESULTS
def _df(job_id):
    j = JOBS.get(job_id)
    return j["df_res"] if j and j["df_res"] is not None else None


@app.route("/api/results/<job_id>/dashboard")
@require_auth
def dashboard(job_id):
    df = _df(job_id)
    if df is None:
        return jsonify({"error": "Résultat indisponible"}), 404
    r = JOBS[job_id]["rapport"]
    return jsonify({
        "rapport": r,
        "statut": df["statut_final"].value_counts().to_dict(),
        "familles": df["a4_famille_n1"].value_counts().head(12).to_dict(),
        "agents": {
            "Nettoyage": int((df["a1_statut"] != "CORROMPU").sum()),
            "Déduplication": int((df["a2_doublon_alerte"] != "").sum()),
            "Normalisation": int(df["a3_desc_conforme"].sum()),
            "Classification": int((~df["a4_validation_requise"]).sum()),
            "Enrichissement": int((df["a5_attributs"] != "aucun_attribut").sum()),
        }})


@app.route("/api/results/<job_id>/validation")
@require_auth
def validation(job_id):
    df = _df(job_id)
    if df is None:
        return jsonify({"error": "Résultat indisponible"}), 404
    v = df[df["statut_final"] == "VALIDATION_HUMAINE"].copy()

    def motif(row):
        rs = []
        if row.get("a4_validation_requise"): rs.append("Classification")
        if row.get("a5_validation_requise"): rs.append("Enrichissement")
        if row.get("a2_doublon_decision") == "VALIDATION_HUMAINE": rs.append("Doublon")
        return " + ".join(rs) if rs else "Autre"
    v["motif"] = v.apply(motif, axis=1)
    rows = v.head(200)[["code_sap", "a3_desc_courte_sap", "a4_famille_n1",
                        "a4_confiance", "motif"]].rename(columns={
        "a3_desc_courte_sap": "description", "a4_famille_n1": "famille",
        "a4_confiance": "confiance"}).to_dict("records")
    return jsonify({"total": int(len(v)),
                    "motifs": v["motif"].value_counts().to_dict(), "rows": rows})


@app.route("/api/results/<job_id>/duplicates")
@require_auth
def duplicates(job_id):
    df = _df(job_id)
    if df is None:
        return jsonify({"error": "Résultat indisponible"}), 404
    d = df[df["a2_doublon_alerte"] != ""].copy()
    rows = d.head(200)[["code_sap", "a3_desc_courte_sap", "a2_doublon_alerte",
                        "a2_est_maitre"]].rename(columns={
        "a3_desc_courte_sap": "description", "a2_doublon_alerte": "doublon",
        "a2_est_maitre": "maitre"}).to_dict("records")
    return jsonify({
        "paires": int((df["a2_doublon_alerte"] != "").sum()),
        "auto": int((df["a2_doublon_decision"] == "AUTO_FUSION").sum()),
        "clusters": int(df["a2_cluster"].nunique()), "rows": rows})


# ── articles codifiés (destination MongoDB) ────────────────────────────
@app.route("/api/results/<job_id>/codifies")
@require_auth
def codifies(job_id):
    df = _df(job_id)
    if df is None:
        return jsonify({"error": "Résultat indisponible"}), 404
    m = df[df["statut_final"] == "CODIFIÉ"].copy()
    rows = m.head(200).apply(lambda r: {
        "code": r["code_sap"], "description": r["a3_desc_courte_sap"],
        "famille": r["a4_famille_n1"], "marque": r["a5_marque"],
        "ref": r["a5_reference_fab"]}, axis=1).tolist()
    return jsonify({"codifies": int(len(m)), "rows": rows})


# ═══════════════════════════════════════════════════════════ SAVE → MONGO
@app.route("/api/save/<job_id>", methods=["POST"])
@require_auth
def save_mongo(job_id):
    df = _df(job_id)
    if df is None:
        return jsonify({"error": "Résultat indisponible"}), 404
    uri = (request.json or {}).get("uri") or MONGO_URI
    staging = (request.json or {}).get("staging", False)
    try:
        ins, mod, tot = marsa_mongo.sauvegarder_codifies(df, uri)
        out = {"ok": True, "collection": marsa_mongo.COLL_CODIFIES,
               "inseres": ins, "modifies": mod, "total": tot}
        if staging:
            si, sm, stt = marsa_mongo.sauvegarder_staging(df, uri)
            out["staging"] = {"inseres": si, "modifies": sm, "total": stt}
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/mongo/test", methods=["POST"])
@require_auth
def mongo_test():
    uri = (request.json or {}).get("uri") or MONGO_URI
    ok, msg = marsa_mongo.tester_connexion(uri)
    return jsonify({"ok": ok, "message": msg})


# ═══════════════════════════════════════════════════════════ DOWNLOAD
@app.route("/api/results/<job_id>/download/<kind>")
@require_auth
def download(job_id, kind):
    df = _df(job_id)
    if df is None:
        return jsonify({"error": "Résultat indisponible"}), 404
    if kind == "validation":
        out = df[df["statut_final"] == "VALIDATION_HUMAINE"]
        nom = "validation_expert.xlsx"
    elif kind == "codifies":
        m = df[df["statut_final"] == "CODIFIÉ"]
        out = pd.DataFrame({"code_article": m["code_sap"],
                            "description": m["a3_desc_courte_sap"],
                            "famille": m["a4_famille_n1"],
                            "marque": m["a5_marque"],
                            "reference": m["a5_reference_fab"]})
        nom = "articles_codifies.xlsx"
    else:
        out, nom = df, "resultat_pipeline.xlsx"
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        out.to_excel(w, index=False, sheet_name="export")
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=nom,
                     mimetype="application/vnd.openxmlformats-officedocument."
                              "spreadsheetml.sheet")


if __name__ == "__main__":
    print("⚓ Marsa Maroc — API sur http://localhost:5000")
    print(f"   Login : {AUTH_USER} / {AUTH_PASS}   |   Mongo : {MONGO_URI}")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
