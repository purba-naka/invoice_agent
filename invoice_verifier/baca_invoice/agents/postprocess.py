"""Post-processing deterministik untuk TravelDocumentResult.

Modul ini berisi logika perhitungan `extraction_confidence`,
`requires_manual_review`, dan `review_reasons` yang sebelumnya dititipkan ke
LLM. Dipindahkan ke kode supaya hasilnya stabil dan testable terpisah dari
agent. Modul ini tidak bergantung pada ADK dan bisa dipakai ulang di tempat
lain (mis. unit test atau pipeline batch).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google.adk.models import LlmResponse
from google.genai import types

from ..models.authenticity import DocumentAuthenticity
from ..models.travel_document import OcrSummary, TravelDocumentResult

logger = logging.getLogger(__name__)

LARGE_PAYMENT_THRESHOLD = 10_000_000
DEFAULT_CURRENCY = "IDR"
EXTRACTOR_STATE_KEY = "document_data"
RAW_AUTHENTICITY_STATE_KEY = "raw_authenticity"


def capture_tool_authenticity(
    tool: Any,  # noqa: ARG001
    args: dict[str, Any],  # noqa: ARG001
    tool_context: Any,
    tool_response: dict[str, Any],
) -> dict[str, Any] | None:
    """ADK after_tool_callback: simpan raw authenticity dict ke state.

    Dipasang di extractor_agent supaya postprocess punya akses ke output tool
    asli (bukan via re-serialisasi LLM yang bisa drop field). Tidak mengubah
    response tool (return None).
    """
    auth = tool_response.get("authenticity") if isinstance(tool_response, dict) else None
    if isinstance(auth, dict):
        try:
            tool_context.state[RAW_AUTHENTICITY_STATE_KEY] = auth
        except Exception as exc:  # noqa: BLE001
            logger.warning("capture_tool_authenticity: gagal tulis state (%s).", exc)
    return None

_COMMON_FIELDS: tuple[str, ...] = (
    "total_payment",
    "payment_method",
    "payment_date_time",
    "currency",
)

_INVOICE_FIELDS: tuple[str, ...] = (
    "invoice_number",
    "issue_date",
    "vendor_name",
    "buyer_name",
    "payment_terms",
    "line_items",
)

_RECEIPT_FIELDS: tuple[str, ...] = (
    "receipt_number",
    "transaction_date",
    "payment_date",
    "merchant_name",
    "payer_name",
    "payment_status",
    "items_purchased",
)

_HOTEL_FIELDS: tuple[str, ...] = (
    "hotel_name",
    "hotel_city",
    "room_type",
    "check_in_date",
    "check_out_date",
    "total_nights",
)

_FLIGHT_FIELDS: tuple[str, ...] = (
    "airline",
    "route_from",
    "route_to",
    "flight_date",
    "traveler_name",
    "ticket_price",
)

_CRITICAL_FIELDS: tuple[str, ...] = ("total_payment", "payment_date_time")


def _is_filled(value: Any) -> bool:
    """True jika field punya nilai bermakna (bukan default kosong)."""
    if value is None or value == "-" or value == "":
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value == 0:
        return False
    if isinstance(value, list) and len(value) == 0:
        return False
    return True


def _relevant_fields(result: TravelDocumentResult) -> tuple[str, ...]:
    fields: list[str] = list(_COMMON_FIELDS)
    if result.doc_type == "invoice":
        fields.extend(_INVOICE_FIELDS)
    elif result.doc_type == "receipt":
        fields.extend(_RECEIPT_FIELDS)
    if result.document_subtype == "hotel":
        fields.extend(_HOTEL_FIELDS)
    elif result.document_subtype == "flight":
        fields.extend(_FLIGHT_FIELDS)
    return tuple(dict.fromkeys(fields))


def compute_confidence(result: TravelDocumentResult) -> float:
    """Hitung confidence dari rasio field relevan yang terisi."""
    if result.doc_type == "unknown":
        return 0.0
    fields = _relevant_fields(result)
    if not fields:
        return 0.0
    filled = sum(1 for f in fields if _is_filled(getattr(result, f, None)))
    ratio = filled / len(fields)
    if ratio > 0.8:
        return 0.85
    if ratio > 0.5:
        return 0.65
    if ratio > 0.2:
        return 0.4
    return 0.2


def compute_review_flags(result: TravelDocumentResult) -> tuple[bool, list[str]]:
    """Tentukan apakah perlu review manual dan alasan-alasannya."""
    reasons: list[str] = []
    if result.doc_type == "unknown":
        reasons.append("doc_type unknown")
    if getattr(result.authenticity, "is_suspicious", False):
        reasons.append("authenticity suspicious")
    if result.total_payment > LARGE_PAYMENT_THRESHOLD:
        reasons.append(f"total_payment > {LARGE_PAYMENT_THRESHOLD:,}")
    if result.doc_type != "unknown":
        missing = [f for f in _CRITICAL_FIELDS if not _is_filled(getattr(result, f, None))]
        if missing:
            reasons.append(f"field penting tidak terbaca: {', '.join(missing)}")

    # OCR-related review flags
    ocr = result.ocr_summary
    if ocr.pages_ocr_failed > 0:
        reasons.append("ocr_failed")
    if ocr.pages_skipped > 0:
        reasons.append("ocr_skipped_pages")
    if ocr.route == "ocr" and not ocr.enabled:
        reasons.append("ocr_unavailable")

    return (bool(reasons), reasons)


def _extract_ocr_summary_from_state(state_payload: Any) -> OcrSummary | None:
    """Ambil OcrSummary dari output extractor (state['document_data'])."""
    if isinstance(state_payload, str):
        try:
            state_payload = json.loads(state_payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(state_payload, dict):
        return None
    raw = state_payload.get("ocr_summary")
    if not isinstance(raw, dict):
        return None
    try:
        return OcrSummary.model_validate(raw)
    except ValueError as exc:
        logger.warning("postprocess: gagal validasi ocr_summary dari state (%s).", exc)
        return None


def _normalize_currency(value: str | None) -> str:
    """Fallback ke IDR jika kosong/`-`."""
    if not value or value.strip() in ("", "-"):
        return DEFAULT_CURRENCY
    return value


def _extract_authenticity_from_state(state_payload: Any) -> DocumentAuthenticity | None:
    """Ambil DocumentAuthenticity dari output extractor (state['document_data']).

    Extractor menyimpan teks JSON `{"full_text":..., "authenticity": {...}, ...}`.
    Kalau parsing gagal, kembalikan None — caller tetap pakai authenticity dari
    LLM (fallback).
    """
    if state_payload is None:
        return None
    if isinstance(state_payload, str):
        try:
            state_payload = json.loads(state_payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(state_payload, dict):
        return None
    auth_raw = state_payload.get("authenticity")
    if not isinstance(auth_raw, dict):
        return None
    try:
        return DocumentAuthenticity.model_validate(auth_raw)
    except ValueError as exc:
        logger.warning("postprocess: gagal validasi authenticity dari state (%s).", exc)
        return None


def apply_postprocessing(
    result: TravelDocumentResult,
    authenticity_override: DocumentAuthenticity | None = None,
    ocr_summary_override: OcrSummary | None = None,
) -> TravelDocumentResult:
    """Pure function: terima TravelDocumentResult, kembalikan versi terisi meta.

    Args:
        result: hasil parse JSON output formatter.
        authenticity_override: jika diberikan, timpa `result.authenticity`
            dengan nilai ini (untuk bypass LLM dan pakai output tool langsung).
        ocr_summary_override: jika diberikan, timpa `result.ocr_summary`
            dengan nilai dari state extractor (selalu ada jika jalur normal).
    """
    updates: dict[str, Any] = {
        "currency": _normalize_currency(result.currency),
    }
    if authenticity_override is not None:
        updates["authenticity"] = authenticity_override
    if ocr_summary_override is not None:
        updates["ocr_summary"] = ocr_summary_override
    intermediate = result.model_copy(update=updates)

    confidence = compute_confidence(intermediate)
    requires_review, reasons = compute_review_flags(intermediate)
    return intermediate.model_copy(
        update={
            "extraction_confidence": confidence,
            "requires_manual_review": requires_review,
            "review_reasons": reasons,
        }
    )


def postprocess_llm_response(
    callback_context: Any,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """ADK after_model_callback: terapkan post-processing ke JSON output formatter.

    Mengembalikan LlmResponse baru dengan text JSON yang sudah diisi field meta
    deterministik dan authenticity di-overwrite dari hasil tool (state extractor).
    Jika parsing gagal, kembalikan None agar response asli lewat apa adanya
    (output_schema akan tetap memvalidasi).
    """
    content = llm_response.content
    if content is None or not content.parts:
        return None

    text_parts = [p for p in content.parts if getattr(p, "text", None)]
    if not text_parts:
        return None

    raw_text = text_parts[0].text or ""
    try:
        payload = json.loads(raw_text)
        result = TravelDocumentResult.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("postprocess: gagal parse output formatter (%s); skip.", exc)
        return None

    auth_override: DocumentAuthenticity | None = None
    ocr_override: OcrSummary | None = None
    state = getattr(callback_context, "state", None)
    if state is not None:
        try:
            raw_auth = state.get(RAW_AUTHENTICITY_STATE_KEY)
            if isinstance(raw_auth, dict):
                auth_override = DocumentAuthenticity.model_validate(raw_auth)
            else:
                auth_override = _extract_authenticity_from_state(state.get(EXTRACTOR_STATE_KEY))
            ocr_override = _extract_ocr_summary_from_state(state.get(EXTRACTOR_STATE_KEY))
        except Exception as exc:  # noqa: BLE001
            logger.warning("postprocess: gagal akses state (%s).", exc)

    processed = apply_postprocessing(result, authenticity_override=auth_override, ocr_summary_override=ocr_override)
    new_text = processed.model_dump_json()
    return LlmResponse(
        content=types.Content(role=content.role, parts=[types.Part(text=new_text)])
    )
