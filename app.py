import os
from pathlib import Path
from typing import Any

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

    for key in ("UPLOAD_FOLDER", "OUTPUT_FOLDER", "TEMPLATES_FOLDER", "DRAFT_TEMPLATES_FOLDER"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    catalog_service = CatalogService(Path(app.config["DATABASE_PATH"]))
    catalog_service.initialize(Path(app.config["TEMPLATES_FOLDER"]))
    app.extensions["catalog_service"] = catalog_service

    cors_origins = app.config.get("CORS_ORIGINS")
    if cors_origins:
        origins = [origin.strip() for origin in cors_origins.split(",") if origin.strip()]
        CORS(app, resources={r"/api/*": {"origins": origins}})

    app.register_blueprint(mockup_routes)
    app.register_blueprint(admin_routes)

    @app.get("/outputs/<path:filename>")
    def output_file(filename: str):
        return send_from_directory(app.config["OUTPUT_FOLDER"], filename)

    @app.get("/templates/<template_id>/<path:filename>")
    def template_file(template_id: str, filename: str):
        relative_path = f"{template_id}/{filename}"
        return send_from_directory(app.config["TEMPLATES_FOLDER"], relative_path)

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
