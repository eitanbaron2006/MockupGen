import logging
import os
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import RequestEntityTooLarge

from config import Config
from routes.admin_routes import admin_routes
from routes.mockup_routes import mockup_routes
from services.catalog_service import CatalogService


def create_app(config_overrides: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)
        if app.config.get("TESTING") and "DATABASE_PATH" not in config_overrides:
            test_root = Path(app.config["OUTPUT_FOLDER"]).parent
            app.config["DATABASE_PATH"] = str(test_root / "data" / "mockup_catalog.sqlite3")
            app.config["DRAFT_TEMPLATES_FOLDER"] = str(test_root / "draft_templates")

    # The session cookie -- and with it the admin's logged-in state and the CSRF
    # token -- is only as good as the key that signs it. The shipped default is
    # published in this repository, so a server running on it is a server anyone
    # can forge a session for: refuse to start rather than pretend to be safe.
    # Debug and test runs are local and keep the convenience.
    if (
        app.config.get("SECRET_KEY") == Config.DEFAULT_SECRET_KEY
        and not app.config.get("TESTING")
        and not app.debug
    ):
        raise RuntimeError(
            "SECRET_KEY is still the development default. Set SECRET_KEY in .env "
            "to a long random value before running the server."
        )

    for key in ("UPLOAD_FOLDER", "OUTPUT_FOLDER", "TEMPLATES_FOLDER", "DRAFT_TEMPLATES_FOLDER"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)

    # Earlier builds saved every admin canvas preview into the outputs folder.
    # Previews are inline now, so sweep the ones already left behind.
    from services.simple_mockup_service import prune_preview_outputs

    prune_preview_outputs(Path(app.config["OUTPUT_FOLDER"]))
    catalog_service = CatalogService(Path(app.config["DATABASE_PATH"]))
    catalog_service.initialize(Path(app.config["TEMPLATES_FOLDER"]))
    app.extensions["catalog_service"] = catalog_service

    # Admin assets were cache-busted by a hand-edited ?v= number, so any edit
    # that forgot to bump it shipped stale JS/CSS to every open browser. The
    # stamp now follows the file itself and cannot drift.
    @app.template_global()
    def asset_version(filename: str) -> str:
        try:
            return str(int((Path(app.static_folder) / filename).stat().st_mtime))
        except OSError:
            return "0"

    from services.telemetry_service import TelemetryService
    telemetry_service = TelemetryService(
        upload_folder=Path(app.config["UPLOAD_FOLDER"]),
        output_folder=Path(app.config["OUTPUT_FOLDER"]),
        templates_folder=Path(app.config["TEMPLATES_FOLDER"]),
    )
    app.extensions["telemetry_service"] = telemetry_service

    import time
    import uuid
    from flask import g, request

    @app.before_request
    def _start_telemetry():
        g.telemetry_start_time = time.perf_counter()
        g.telemetry_request_id = str(uuid.uuid4())[:8]

    @app.after_request
    def _record_telemetry(response):
        start = getattr(g, "telemetry_start_time", None)
        req_id = getattr(g, "telemetry_request_id", "req")
        duration_ms = (time.perf_counter() - start) * 1000.0 if start else 0.0

        is_telemetry_poll = request.path in (
            "/api/telemetry/summary",
            "/api/telemetry/requests",
            "/api/telemetry/errors",
        )
        if not is_telemetry_poll:
            req_size = request.content_length or 0
            resp_size = response.calculate_content_length() or 0
            client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "127.0.0.1").split(",")[0].strip()

            telemetry: TelemetryService | None = app.extensions.get("telemetry_service")
            if telemetry:
                err_msg = None
                if response.status_code >= 400:
                    try:
                        if response.is_json:
                            err_msg = response.get_json().get("error")
                    except Exception:
                        pass
                telemetry.record_request(
                    request_id=req_id,
                    method=request.method,
                    path=request.path,
                    status=response.status_code,
                    duration_ms=duration_ms,
                    client_ip=client_ip,
                    request_size=req_size,
                    response_size=resp_size,
                    error=err_msg,
                )
                if response.status_code >= 400 and not is_telemetry_poll:
                    telemetry.record_error(
                        request_id=req_id,
                        method=request.method,
                        path=request.path,
                        status=response.status_code,
                        error_message=err_msg or f"HTTP {response.status_code}",
                        client_ip=client_ip,
                    )
        return response

    @app.teardown_request
    def _record_telemetry_exception(exc: BaseException | None):
        if exc:
            telemetry: TelemetryService | None = app.extensions.get("telemetry_service")
            if telemetry:
                req_id = getattr(g, "telemetry_request_id", "err")
                client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "127.0.0.1").split(",")[0].strip()
                telemetry.record_error(
                    request_id=req_id,
                    method=request.method,
                    path=request.path,
                    status=500,
                    exc=exc if isinstance(exc, Exception) else None,
                    error_message=str(exc),
                    client_ip=client_ip,
                )

    cors_origins = app.config.get("CORS_ORIGINS")
    if cors_origins:
        origins = [origin.strip() for origin in cors_origins.split(",") if origin.strip()]
        # Browser clients fetch rendered outputs and template previews
        # cross-origin too, so CORS must cover the file routes as well.
        CORS(app, resources={
            r"/api/*": {"origins": origins},
            r"/outputs/*": {"origins": origins},
            r"/templates/*": {"origins": origins},
        })

    app.register_blueprint(mockup_routes)
    app.register_blueprint(admin_routes)

    @app.get("/outputs/<path:filename>")
    def output_file(filename: str):
        return send_from_directory(app.config["OUTPUT_FOLDER"], filename)

    @app.get("/templates/<template_id>/<path:filename>")
    def template_file(template_id: str, filename: str):
        relative_path = f"{template_id}/{filename}"
        return send_from_directory(app.config["TEMPLATES_FOLDER"], relative_path)

    @app.get("/favicon.ico")
    def favicon_file():
        return send_from_directory(
            os.path.join(app.root_path, "static"),
            "favicon.ico",
            mimetype="image/vnd.microsoft.icon",
        )

    @app.errorhandler(RequestEntityTooLarge)
    def handle_large_upload(_error: RequestEntityTooLarge):
        return jsonify({"success": False, "error": "Upload exceeds size limit"}), 413

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    )
