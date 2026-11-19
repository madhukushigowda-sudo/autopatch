import pytest

from autopatch import create_app
from autopatch.core import analysis as analysis_svc
from tests.helpers import upload_files


@pytest.fixture()
def app(tmp_path):
    return create_app({"DATA_DIR": tmp_path / "data", "STORAGE_BACKEND": "local"})


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def ctx(app):
    ext = app.extensions["autopatch"]
    return ext["store"], ext["cfg"]


@pytest.fixture()
def analyze(client, ctx):
    """analyze(files, options=None, **cfg_overrides) -> (run, findings, project_id). Synchronous (no thread)."""
    store, cfg = ctx

    def _analyze(files, options=None, **overrides):
        for key, value in overrides.items():
            setattr(cfg, key, value)
        pid = upload_files(client, files)
        run = analysis_svc.start_analysis(store, cfg, pid, options, background=False)
        findings = sorted(store.find("findings", {"run_id": run["_id"]}), key=lambda f: f["finding_id"])
        return run, findings, pid

    return _analyze
