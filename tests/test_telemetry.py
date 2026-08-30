from pathlib import Path

from app import create_app
from services.telemetry_service import TelemetryService


def test_telemetry_service_unit(tmp_path: Path):
    upload_dir = tmp_path / "uploads"
    output_dir = tmp_path / "outputs"
    templates_dir = tmp_path / "templates"
    for d in (upload_dir, output_dir, templates_dir):
        d.mkdir()

    service = TelemetryService(
        upload_folder=upload_dir,
        output_folder=output_dir,
        templates_folder=templates_dir,
    )

    # 1. Record requests
    service.record_request("req1", "GET", "/api/health", 200, 15.2, "127.0.0.1")
    service.record_request("req2", "POST", "/api/mockup/generate", 200, 240.5, "127.0.0.1")
    service.record_request("req3", "GET", "/non-existent", 404, 5.0, "127.0.0.1", error="Not Found")
    service.record_request("req4", "POST", "/api/crash", 500, 12.0, "127.0.0.1", error="Internal Crash")

    # 2. Record error
    try:
        raise ValueError("Simulated render exception")
    except Exception as exc:
        service.record_error("err1", "POST", "/api/mockup/generate", 500, exc=exc)

    # 3. Check summary
    summary = service.get_summary()
    assert summary["requests"]["total"] == 4
    assert summary["requests"]["status_2xx"] == 2
    assert summary["requests"]["status_4xx"] == 1
    assert summary["requests"]["status_5xx"] == 1
    assert summary["requests"]["success_rate"] == 50.0  # 2 success out of 4
    assert summary["errors_logged"] == 1
    assert summary["system"]["active_threads"] >= 1

    # 4. Check filtered requests
    all_reqs = service.get_recent_requests()
    assert len(all_reqs) == 4

    status_2xx_reqs = service.get_recent_requests(status_filter="2xx")
    assert len(status_2xx_reqs) == 2

    status_4xx_reqs = service.get_recent_requests(status_filter="4xx")
    assert len(status_4xx_reqs) == 1

    errors = service.get_recent_errors()
    assert len(errors) == 1
    assert "Simulated render exception" in errors[0]["error_message"]
    assert "ValueError" in errors[0]["error_type"]
    assert "Traceback" in errors[0]["traceback"]

    # 5. Clear logs
    service.clear_logs()
    summary_after = service.get_summary()
    assert summary_after["requests"]["total"] == 0
    assert len(service.get_recent_requests()) == 0


def test_telemetry_api_routes(tmp_path: Path):
    upload_dir = tmp_path / "uploads"
    output_dir = tmp_path / "outputs"
    templates_dir = tmp_path / "templates"
    drafts_dir = tmp_path / "draft_templates"
    db_path = tmp_path / "catalog.sqlite3"

    for d in (upload_dir, output_dir, templates_dir, drafts_dir):
        d.mkdir()

    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "ADMIN_PASSWORD": "admin-pass",
        "UPLOAD_FOLDER": str(upload_dir),
        "OUTPUT_FOLDER": str(output_dir),
        "TEMPLATES_FOLDER": str(templates_dir),
        "DRAFT_TEMPLATES_FOLDER": str(drafts_dir),
        "DATABASE_PATH": str(db_path),
    })

    client = app.test_client()

    # 0. The dashboard is an admin surface: the page sends a stranger to the
    # login screen, the readings refuse to be read, and the two endpoints that
    # delete files refuse twice over -- they are behind the CSRF check as well.
    assert client.get("/server-pulse").status_code == 302
    for path in ("/api/telemetry/summary", "/api/telemetry/requests", "/api/telemetry/errors"):
        assert client.get(path).status_code == 401, path
    for path in ("/api/telemetry/purge-temp", "/api/telemetry/clear-logs"):
        assert client.post(path).status_code == 401, path

    login = client.post("/api/admin/login", json={"password": "admin-pass"})
    assert login.status_code == 200
    csrf = login.get_json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    # Logged in, but a POST still has to prove where it came from.
    assert client.post("/api/telemetry/purge-temp").status_code == 403
    assert client.post("/api/telemetry/clear-logs").status_code == 403

    # 1. Access /server-pulse HTML page
    page_resp = client.get("/server-pulse")
    assert page_resp.status_code == 200
    assert b"Mockup" in page_resp.data
    assert b"Pulse" in page_resp.data

    # 2. Trigger an API call that gets recorded
    health_resp = client.get("/api/health")
    assert health_resp.status_code == 200

    # 3. Check telemetry summary API
    summary_resp = client.get("/api/telemetry/summary")
    assert summary_resp.status_code == 200
    summary_json = summary_resp.get_json()
    assert summary_json["success"] is True
    assert summary_json["data"]["requests"]["total"] >= 1

    # 4. Check telemetry requests API
    reqs_resp = client.get("/api/telemetry/requests")
    assert reqs_resp.status_code == 200
    reqs_json = reqs_resp.get_json()
    assert reqs_json["success"] is True
    assert any(r["path"] == "/api/health" for r in reqs_json["requests"])

    # 5. Check purge temp files API
    purge_resp = client.post("/api/telemetry/purge-temp?max_age_hours=0", headers=headers)
    assert purge_resp.status_code == 200
    assert purge_resp.get_json()["success"] is True

    # 6. Check clear logs API
    clear_resp = client.post("/api/telemetry/clear-logs", headers=headers)
    assert clear_resp.status_code == 200
    assert clear_resp.get_json()["success"] is True

    # 7. Check providers status API (requires admin authentication)
    prov_resp = client.get("/api/admin/providers/status")
    assert prov_resp.status_code == 200
    prov_json = prov_resp.get_json()
    assert prov_json["success"] is True
    assert "classic" in prov_json["providers"]
    assert "vertex" in prov_json["providers"]
    assert prov_json["providers"]["classic"]["available"] is True
