# Historical before-fix reproduction; evidence retained in security_audit_evidence.json.
if __name__ == '__main__':
    raise SystemExit('Historical probe targets pre-fix code. Use docs/security_verification.py for the secured implementation.')

"""Reproduce the 2026-10-04 audit using synthetic data and temporary storage only.

Run from any directory with the project's test dependencies installed:
    .venv/bin/python docs/security_audit_probe.py
The script refreshes security_audit_evidence.json next to this file. It never
opens data/local, reads real patient records, or starts a network listener.
HTTP status codes describe the inspected snapshot, not a security certification.
"""
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from backend.app import create_app
from scripts import bundle_project


def main():
    inputs = {"age_years": 42, "sex": "F", "hemoglobin": 108}
    with tempfile.TemporaryDirectory(prefix="hema-audit-") as temp:
        root = Path(temp)
        app = create_app(data_dir=root, ocr_mode="off", model_bundle=None)
        with TestClient(app, base_url="http://localhost") as client:
            result = client.post("/api/patient/predict", json={"inputs": inputs})
            assert result.status_code == 200, result.text
            report = result.json()
            content = io.BytesIO()
            pdf = canvas.Canvas(content)
            lines = ["Patient: SECURITY AUDIT SYNTHETIC", "Date of birth: 01.01.1980",
                     "Collection date: 01.10.2026", "Hb 108 g/L"]
            for index, line in enumerate(lines):
                pdf.drawString(30, 800 - index * 20, line)
            pdf.save()
            upload = client.post("/api/observations/pdf", files={
                "files": ("synthetic-person.pdf", content.getvalue(), "application/pdf")})
            assert upload.status_code == 200, upload.text
            observation = upload.json()
            obs_id = observation["observationId"]
            doc = observation["observation"]["documents"][0]
            doctor_status = client.post("/api/doctor/predict", json={"inputs": inputs}).status_code
            # There is no account, cookie, bearer token or consent in this client.
            other_app = create_app(data_dir=root, ocr_mode="off", model_bundle=None)
            with TestClient(other_app, base_url="http://localhost") as other:
                statuses = {
                    "report": other.get("/api/reports/" + report["reportId"]).status_code,
                    "observation": other.get("/api/observations/" + obs_id).status_code,
                    "original_pdf": other.get("/api/observations/" + obs_id +
                                              "/documents/" + doc["id"]).status_code,
                    "update_review": other.put("/api/observations/" + obs_id + "/review", json={
                        "revision": observation["revision"],
                        "inputs": {**inputs, "hemoglobin": 109}}).status_code,
                }
                # A hypothetical pre-authentication doctor record has no owner.
                legacy_id = str(uuid4())
                other_app.state.store.put("report", {"id": legacy_id, "audience": "doctor"})
                other_app.state.auth.create_user("audit.doctor", "Synthetic-audit-password-only")
                login = other.post("/api/auth/login", json={
                    "username": "audit.doctor", "password": "Synthetic-audit-password-only"},
                    headers={"X-Hema-Client": "1"})
                assert login.status_code == 200, login.text
                legacy_status = other.get("/api/reports/" + legacy_id).status_code
            with sqlite3.connect(root / "hema.sqlite3") as db:
                stored_json = db.execute("SELECT payload FROM objects WHERE kind=? AND id=?",
                                         ("observation", obs_id)).fetchone()[0]
            routes = client.get("/openapi.json").json()["paths"]
            findings = {
                "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "all_inputs_synthetic": True,
                "existing_patient_storage_accessed": False,
                "model_inference_enabled_for_probe": False,
                "without_consent_predict_status": result.status_code,
                "without_consent_upload_status": upload.status_code,
                "doctor_predict_without_login_status": doctor_status,
                "independent_client_read_statuses": {k: v for k, v in statuses.items() if k != "update_review"},
                "independent_client_update_review_status": statuses["update_review"],
                "hypothetical_legacy_doctor_report_read_by_new_account_status": legacy_status,
                "patient_name_in_metadata": bool(doc.get("metadata", {}).get("patient_names")),
                "birth_date_in_metadata": bool(doc.get("metadata", {}).get("birth_dates")),
                "patient_name_persisted_in_sqlite": "SECURITY AUDIT SYNTHETIC" in stored_json,
                "original_pdf_persisted": len(list((root / "uploads").rglob("*.pdf"))) == 1,
                "deletion_endpoint_exists": any("delete" in entry for entry in routes.values()),
                "report_response_cache_control": result.headers.get("cache-control"),
            }
        # Inspect the collector with a simulated project, without creating a ZIP.
        simulated = root / "simulated-project"
        for name in ("local", "local-demo"):
            path = simulated / "data" / name / "hema.sqlite3"
            path.parent.mkdir(parents=True)
            path.write_text("SYNTHETIC AUDIT PLACEHOLDER", encoding="utf-8")
        with patch.object(bundle_project, "ROOT", simulated):
            collected = [p.relative_to(simulated).as_posix() for p in bundle_project.files()]
        findings["bundle_default_storage_excluded"] = "data/local/hema.sqlite3" not in collected
        findings["bundle_alternative_storage_included"] = "data/local-demo/hema.sqlite3" in collected
        paths = ["backend/app.py", "backend/auth.py", "backend/store.py", "backend/model_service.py",
                 "lab_parser/parsing.py", "frontend/src/information.js", "frontend/src/app.js",
                 "frontend/src/pdf-report.js", "scripts/bundle_project.py", "tests/test_auth.py"]
        findings["source_sha256"] = {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}
        destination = Path(__file__).with_name("security_audit_evidence.json")
        destination.write_text(json.dumps(findings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(findings, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
