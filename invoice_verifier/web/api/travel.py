from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from web.config import TRAVEL_API_KEY, UPLOAD_DIR
from web.dependencies import get_runner_service
from web.models.travel_contract import TravelResultResponse, TravelSubmitRequest, TravelSubmitResponse
from web.services.agent_runner import JobStatus, classify_document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/travel", tags=["travel"])

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _verify_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    if not TRAVEL_API_KEY:
        return
    if not api_key or api_key != TRAVEL_API_KEY:
        raise HTTPException(status_code=401, detail="X-API-Key tidak valid atau tidak ada.")


# Maps job_id → metadata dari PISmart (reference_id, document_type, submitted_at)
_travel_meta: dict[str, dict] = {}


@router.post(
    "/submit",
    response_model=TravelSubmitResponse,
    summary="Kirim dokumen travel untuk diverifikasi",
    description=(
        "Terima dokumen PDF (invoice/receipt) dari PISmart, jalankan OCR & verifikasi AI, "
        "kembalikan transaction_id. Gunakan GET /api/travel/result/{transaction_id} untuk "
        "mengambil hasilnya."
    ),
)
async def submit_document(
    body: TravelSubmitRequest,
    request: Request,
    runner_service=Depends(get_runner_service),
    _: None = Depends(_verify_api_key),
) -> JSONResponse:
    # Validasi nama file
    filename = Path(body.filename).name
    if not filename or not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="filename harus berakhiran .pdf")

    # Decode base64
    try:
        pdf_bytes = base64.b64decode(body.file_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="file_base64 bukan base64 yang valid.")

    if len(pdf_bytes) < 5 or pdf_bytes[:4] != b"%PDF":
        raise HTTPException(status_code=400, detail="Konten bukan file PDF yang valid.")

    transaction_id = str(uuid.uuid4())
    dest_dir = UPLOAD_DIR / transaction_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename

    if not dest_path.resolve().is_relative_to(UPLOAD_DIR.resolve()):
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Nama file tidak valid.")

    try:
        dest_path.write_bytes(pdf_bytes)
    except OSError as e:
        shutil.rmtree(dest_dir, ignore_errors=True)
        logger.error("Gagal tulis file untuk transaction %s: %s", transaction_id, e)
        raise HTTPException(status_code=500, detail="Gagal menyimpan file.")

    # Klasifikasi dokumen travel (flight/hotel) — tetap auto-detect dari konten PDF
    doc_type = classify_document(filename, str(dest_path.resolve()))
    doc_type_fallback = False
    if doc_type == "unknown":
        # Jangan jadi stopper — fallback ke hotel, tandai dengan warning
        doc_type = "hotel"
        doc_type_fallback = True
        logger.warning(
            "transaction %s: doc_type tidak terdeteksi, fallback ke 'hotel'", transaction_id
        )

    runner_service.create_job(
        job_id=transaction_id,
        file_path=str(dest_path.resolve()),
        filename=filename,
        doc_type=doc_type,
    )

    submitted_at = datetime.now(timezone.utc)
    _travel_meta[transaction_id] = {
        "reference_id": body.reference_id,
        "document_type": body.document_type,  # invoice / receipt dari PISmart
        "source_system": body.source_system,
        "submitted_at": submitted_at,
        "doc_type_fallback": doc_type_fallback,
    }

    asyncio.create_task(runner_service.run_job(transaction_id))

    return JSONResponse({
        "transaction_id": transaction_id,
        "reference_id": body.reference_id,
        "status": "processing",
        "submitted_at": submitted_at.isoformat(),
    })


@router.get(
    "/result/{transaction_id}",
    response_model=TravelResultResponse,
    summary="Ambil hasil verifikasi berdasarkan transaction_id",
    description=(
        "Poll endpoint ini setelah POST /submit. status='processing' artinya masih berjalan. "
        "status='completed' artinya hasil tersedia di field 'result'. "
        "Jika OCR confidence rendah, field 'warning' akan diisi — hasil tetap dikembalikan "
        "(tidak jadi stopper)."
    ),
)
async def get_result(
    transaction_id: str,
    runner_service=Depends(get_runner_service),
    _: None = Depends(_verify_api_key),
) -> JSONResponse:
    meta = _travel_meta.get(transaction_id)
    if not meta:
        raise HTTPException(status_code=404, detail="transaction_id tidak ditemukan.")

    job = runner_service.get_job(transaction_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    reference_id = meta["reference_id"]
    document_type = meta["document_type"]

    if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
        return JSONResponse({
            "transaction_id": transaction_id,
            "reference_id": reference_id,
            "document_type": document_type,
            "status": "processing",
            "ocr_confidence": None,
            "result": None,
            "warning": None,
            "error": None,
            "completed_at": None,
        })

    completed_at = datetime.now(timezone.utc).isoformat()

    if job.status == JobStatus.ERROR:
        # Jangan jadi stopper — return status failed, bukan raise exception
        return JSONResponse({
            "transaction_id": transaction_id,
            "reference_id": reference_id,
            "document_type": document_type,
            "status": "failed",
            "ocr_confidence": None,
            "result": None,
            "warning": None,
            "error": job.error or "Verifikasi gagal.",
            "completed_at": completed_at,
        })

    result = job.result or {}
    ocr_confidence = _extract_confidence(result)
    doc_type_fallback = meta.get("doc_type_fallback", False)
    warning = _build_warning(ocr_confidence, result, doc_type_fallback)

    return JSONResponse({
        "transaction_id": transaction_id,
        "reference_id": reference_id,
        "document_type": document_type,
        "status": "completed",
        "ocr_confidence": ocr_confidence,
        "result": result,
        "warning": warning,
        "error": None,
        "completed_at": completed_at,
    })


def _extract_confidence(result: dict) -> float | None:
    """Ambil extraction_confidence dari hasil agent."""
    if not isinstance(result, dict):
        return None
    confidence = result.get("extraction_confidence")
    if isinstance(confidence, (int, float)):
        return float(confidence)
    return None


def _build_warning(
    confidence: float | None,
    result: dict,
    doc_type_fallback: bool = False,
) -> str | None:
    """Bangun pesan warning jika confidence rendah, ada flag kecurigaan, atau doc_type fallback."""
    warnings: list[str] = []

    if doc_type_fallback:
        warnings.append("Jenis dokumen tidak terdeteksi otomatis, diproses sebagai 'hotel'.")

    if confidence is not None and confidence < 0.6:
        warnings.append(f"OCR confidence rendah ({confidence:.0%}), disarankan review manual.")

    authenticity = result.get("authenticity") if isinstance(result, dict) else None
    if isinstance(authenticity, dict):
        verdict = authenticity.get("verdict", "")
        if verdict in ("MENCURIGAKAN", "PALSU/DIEDIT"):
            warnings.append(f"Dokumen terdeteksi: {verdict}.")
        flags = authenticity.get("warning_flags") or []
        if flags:
            warnings.append(f"Warning flags: {', '.join(flags)}.")

    return " ".join(warnings) if warnings else None
