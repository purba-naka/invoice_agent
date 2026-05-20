from __future__ import annotations

import os

# ── Stub heavy optional dependencies before any web.* import ──────────────
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

_APP_ROOT = Path(__file__).resolve().parents[1] / "invoice_verifier"
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

_STUB_MODS = [
    "fitz",
    "google", "google.adk", "google.adk.runners", "google.adk.sessions",
    "google.adk.agents",
    "google.genai", "google.genai.types",
    # baca_invoice package itself is NOT stubbed so real submodules (models.*) load fine.
    # Only stub the submodules that pull in google-adk or fitz at import time.
    "baca_invoice.agent",
    "baca_invoice.agents",
    "baca_invoice.agents.document", "baca_invoice.agents.formatter",
    "baca_invoice.agents.flight", "baca_invoice.agents.hotel",
    "baca_invoice.agents.invoice", "baca_invoice.agents.receipt",
    "baca_invoice.tools", "baca_invoice.tools.constants",
    "baca_invoice.tools.combined", "baca_invoice.tools.authenticity",
    "baca_invoice.tools.pdf",
]
for _name in _STUB_MODS:
    sys.modules.setdefault(_name, MagicMock())

# Set env vars BEFORE web.config is imported so Path values are correct.
_tmp_upload = tempfile.mkdtemp(prefix="pinter_test_uploads_")
_tmp_db_fd, _tmp_db_path = tempfile.mkstemp(suffix=".db", prefix="pinter_test_")
os.close(_tmp_db_fd)
os.environ.setdefault("UPLOAD_DIR", _tmp_upload)
os.environ.setdefault("SQLITE_DB_PATH", _tmp_db_path)
os.environ.setdefault("APP_ENV", "development")

# ── Regular imports after stubs are in place ──────────────────────────────
import asyncio  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from web.services.agent_runner import Job, JobStatus  # noqa: E402


# ── Mock runner service (avoids real AI calls) ─────────────────────────────
class MockRunnerService:
    """Synchronous stub for AgentRunnerService — no ADK/Gemini calls."""

    active_job_count = 0

    def __init__(self):
        self._jobs: dict[str, Job] = {}

    def create_job(
        self,
        job_id: str,
        file_path: str,
        filename: str,
        doc_type: str = "invoice",
        sub_type: str | None = None,
    ):
        self._jobs[job_id] = Job(
            job_id=job_id, filename=filename, file_path=file_path,
            doc_type=doc_type, sub_type=sub_type,
        )

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    async def run_job(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job:
            job.status = JobStatus.DONE
            job.result = {"verdict": "AUTENTIK", "summary": "Test OK", "total_payment": 0.0}
            job.push({"type": "complete", "result": job.result})

    async def eviction_loop(self) -> None:
        await asyncio.sleep(86400)


# ── Session-scoped DB init ─────────────────────────────────────────────────
@pytest.fixture(scope="session", autouse=True)
async def _init_db():
    from web.db.sqlite import init_db
    await init_db()


# ── Per-test fixtures ──────────────────────────────────────────────────────
@pytest.fixture
def mock_runner():
    return MockRunnerService()


@pytest.fixture
def stub_classify(monkeypatch):
    """Stub both classifiers so minimal test PDFs pass without real PDF parsing."""
    monkeypatch.setattr("web.api.v1_upload.classify_document", lambda fn, fp: "invoice")
    monkeypatch.setattr("web.api.v1_upload.classify_sub_type", lambda fn, fp: "hotel")


@pytest.fixture(autouse=True)
def clear_rate_limiter():
    """Reset the in-process rate-limit counters between tests."""
    import web.api.v1_upload as m
    m._ip_timestamps.clear()
    yield
    m._ip_timestamps.clear()


@pytest.fixture
async def client(mock_runner, stub_classify):
    """Async HTTPX client backed by the FastAPI app with a mock runner service."""
    from web.dependencies import get_runner_service
    from web.main import app

    app.state.runner_service = mock_runner
    app.dependency_overrides[get_runner_service] = lambda: mock_runner

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def pdf_bytes() -> bytes:
    return b"%PDF-1.4 1 0 obj << /Type /Catalog >> endobj"


@pytest.fixture
async def uploaded_trx(client, pdf_bytes) -> str:
    """Upload a minimal PDF and return trx_id."""
    resp = await client.post(
        "/api/pinter/upload",
        files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200
    return resp.json()["trx_id"]
