"""AutoPatch application factory."""
from __future__ import annotations

import json
from pathlib import Path
import logging

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from config import Config

from .api import analysis, insights, pages, patches, projects
from .core.analysis import recover_interrupted_runs
from .core.gemini_client import GeminiClient
from .core.gemini_rest import select_llm
from .core.guarded_llm import GuardedLLM
from .core.keycheck import apply_to_config as check_gemini_key
from .core.patches import recover_interrupted_patches
from .core.projects import cleanup_tmp
from .core.security import build_csp, host_allowed, security_headers
from .core.workspace import UploadError
from .store import create_store


def create_app(config_overrides: dict | None = None) -> Flask:
    cfg = Config(**(config_overrides or {}))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = Flask(
        __name__,
        template_folder=str(cfg.BASE_DIR / "templates"),
        static_folder=str(cfg.BASE_DIR / "static"),
    )
    app.config.from_object(cfg)
    app.extensions["autopatch"] = {"cfg": cfg, "store": create_store(cfg)}

    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_tmp(cfg)
    recovered = recover_interrupted_runs(app.extensions["autopatch"]["store"])
    if recovered:
        app.logger.warning("Marked %d interrupted analysis run(s) as failed", recovered)

    restored = recover_interrupted_patches(app.extensions["autopatch"]["store"], cfg)
    if restored:
        app.logger.warning("Recovered %d interrupted patch application(s)", restored)

    # Gemini transport: the protected patch API uses ext["llm"] when present, so this swaps only how text is fetched.
    key_check = check_gemini_key(cfg)  # blank/placeholder/malformed keys are blanked here and explained by the guard
    if key_check["problem"]:
        app.logger.warning("GEMINI_API_KEY problem: %s", key_check["problem"])  # the code only, never the value
    llm = select_llm(cfg)
    # Every Gemini request goes through the guard (key check + secret scan), whichever transport is active.
    app.extensions["autopatch"]["llm"] = GuardedLLM(llm if llm is not None else GeminiClient(cfg), cfg)
    app.logger.info("Gemini transport: %s", "built-in HTTPS client" if llm is not None else "google-genai SDK")

    app.register_blueprint(projects.bp)
    app.register_blueprint(analysis.bp)
    app.register_blueprint(patches.bp)
    app.register_blueprint(insights.bp)
    app.register_blueprint(pages.bp)

    csp = build_csp(Path(app.template_folder))
    sec_headers = security_headers(csp)
    extra_hosts = list(getattr(cfg, "ALLOWED_HOSTS", []) or [])

    @app.before_request
    def _only_local_hosts():
        """Reject a Host header that is not a local name (DNS-rebinding defence); applies to pages, API and static files."""
        if not host_allowed(request.host, extra_hosts):
            if request.path.startswith("/api/"):
                return jsonify(error="This server only answers requests addressed to localhost or 127.0.0.1."), 403
            return ("This server only answers requests addressed to localhost or 127.0.0.1.", 403, {"Content-Type": "text/plain; charset=utf-8"})

    @app.after_request
    def _security_headers(resp):
        for k, v in sec_headers.items():
            resp.headers.setdefault(k, v)
        return resp

    @app.after_request
    def _ai_status_transport(resp):
        """With the HTTPS client there is no SDK to install: report the AI client as available and say which transport is used.
        (The unchanged patches UI reads ``sdk_installed`` as "an AI client is available".)"""
        if request.path == "/api/ai/status" and resp.status_code == 200 and resp.is_json:
            data = resp.get_json()
            using_rest = getattr(app.extensions["autopatch"].get("llm"), "transport", None) == "rest"
            data["key_problem"] = getattr(cfg, "GEMINI_KEY_PROBLEM", None)
            data["key_message"] = getattr(cfg, "GEMINI_KEY_MESSAGE", None)
            data["key_note"] = getattr(cfg, "GEMINI_KEY_NOTE", None)
            data["transport"] = "rest" if using_rest else "sdk"
            if using_rest:
                data["sdk_installed"] = True
            resp.set_data(json.dumps(data))
        return resp

    def wants_json() -> bool:
        return request.path.startswith("/api/")

    @app.errorhandler(UploadError)
    def _upload_error(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(RequestEntityTooLarge)
    def _too_large(_exc):
        return jsonify(error=f"The upload is larger than the {cfg.MAX_UPLOAD_MB} MB limit."), 413

    @app.errorhandler(HTTPException)
    def _http_error(exc):
        if wants_json():
            return jsonify(error=exc.description or exc.name), exc.code
        return exc

    @app.errorhandler(Exception)
    def _unhandled(exc):
        app.logger.exception("Unhandled error")
        if wants_json():
            return jsonify(error="Internal server error."), 500
        return "Internal server error.", 500

    return app
