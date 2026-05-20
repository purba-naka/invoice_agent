import os
import secrets
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PROJECT_ROOT.parent / ".env", override=False)
load_dotenv(PROJECT_ROOT / "baca_invoice" / ".env", override=False)


def _path_from_env(name: str, default: Path) -> Path:
    raw_value = os.getenv(name)
    path = Path(raw_value) if raw_value else default
    return path if path.is_absolute() else PROJECT_ROOT / path


APP_ENV: Literal["development", "production"] = os.getenv("APP_ENV", "development")  # type: ignore[assignment]
IS_PRODUCTION: bool = APP_ENV == "production"

ENABLE_DOCS: bool = os.getenv("ENABLE_DOCS", "1").lower() not in ("0", "false", "no")

UPLOAD_DIR: Path = _path_from_env("UPLOAD_DIR", PROJECT_ROOT / "tmp_uploads")
APP_NAME: str = "invoice_verifier"
MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "20"))
MAX_CONCURRENT_JOBS: int = int(os.getenv("MAX_CONCURRENT_JOBS", "10"))
JOB_TTL_SECONDS: int = int(os.getenv("JOB_TTL_SECONDS", "3600"))
JOB_SECRET_KEY: str = os.getenv("JOB_SECRET_KEY") or secrets.token_hex(32)

SQLITE_DB_PATH: str = str(
    _path_from_env("SQLITE_DB_PATH", PROJECT_ROOT / "data" / "invoice_verifier.db")
)

PINTER_API_KEY: str | None = os.getenv("PINTER_API_KEY") or None
PINTER_TRX_TTL_DAYS: int = int(os.getenv("PINTER_TRX_TTL_DAYS", "7"))
