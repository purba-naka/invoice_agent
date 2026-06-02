"""analyze_document: satu-satunya entry point untuk extractor_agent.

Pipeline:
  1. Baca PDF via PyMuPDF (sync, cepat) — ambil pages + metadata.
  2. Hitung routing: mayoritas halaman tanpa text-layer → OCR seluruh dokumen.
  3. Jalur PyMuPDF: gunakan full_text existing.
  4. Jalur OCR: render semua halaman ke PNG (in-memory), kirim paralel ke Gemini,
     gabung teks urut halaman.
  5. Jalankan analyze_authenticity (sama untuk kedua jalur).
  6. Return dict termasuk ocr_summary — selalu hadir.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from typing import Any

from .authenticity import analyze_authenticity
from .ocr import PageOcrInput, PageOcrOutput, ocr_service
from .pdf import read_pdf, render_pages_png

logger = logging.getLogger(__name__)

# Config dibaca dari env saat module load (sama dengan pola pdf.py & authenticity.py)
_OCR_ENABLED: bool = os.getenv("OCR_ENABLED", "1").lower() not in ("0", "false", "no")
_OCR_MIN_TEXT_CHARS: int = int(os.getenv("OCR_MIN_TEXT_CHARS", "20"))
_OCR_MAX_PAGES: int = int(os.getenv("OCR_MAX_PAGES", "10"))
_OCR_RENDER_DPI: int = int(os.getenv("OCR_RENDER_DPI", "200"))
_OCR_MODEL: str = os.getenv("OCR_MODEL", "gemini-2.5-flash")


def compute_route(pages: list[dict[str, Any]], min_chars: int = _OCR_MIN_TEXT_CHARS) -> str:
    """Tentukan jalur ekstraksi untuk dokumen ini.

    Returns:
        "ocr" jika mayoritas (atau tie 50:50) halaman tanpa text-layer,
        "pymupdf" selain itu.
    """
    total = len(pages)
    if total == 0:
        return "pymupdf"
    no_text = sum(len(p.get("text", "").strip()) < min_chars for p in pages)
    return "ocr" if no_text >= math.ceil(total / 2) else "pymupdf"


def _build_ocr_summary_pymupdf(total_pages: int, enabled: bool) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "route": "pymupdf",
        "total_pages": total_pages,
        "pages_text_layer": total_pages,
        "pages_ocr_success": 0,
        "pages_ocr_failed": 0,
        "pages_skipped": 0,
        "ocr_duration_ms": 0,
        "model": "",
    }


def _build_ocr_summary_ocr(
    total_pages: int,
    outputs: list[Any],
    pages_skipped: int,
    enabled: bool,
    model: str,
) -> dict[str, Any]:
    success = sum(o.error is None for o in outputs)
    failed = sum(o.error is not None for o in outputs)
    duration_ms = max((o.duration_ms for o in outputs), default=0)
    return {
        "enabled": enabled,
        "route": "ocr",
        "total_pages": total_pages,
        "pages_text_layer": 0,
        "pages_ocr_success": success,
        "pages_ocr_failed": failed,
        "pages_skipped": pages_skipped,
        "ocr_duration_ms": duration_ms,
        "model": model,
    }


async def analyze_document(file_path: str) -> dict[str, Any]:
    """Ekstrak teks DAN analisis keaslian dokumen PDF dalam satu panggilan.

    File dibuka sekali untuk routing, lalu halaman di-OCR jika diperlukan.
    Tool ini dipakai langsung oleh `extractor_agent` (async ADK tool).

    Args:
        file_path: Path absolut ke file PDF lokal.

    Returns:
        dict dengan key: success, file_path, total_pages, full_text,
        authenticity, ocr_summary. Saat gagal baca PDF, success=False + error.
    """
    # Phase A: Baca PDF (sync)
    content = read_pdf(file_path)
    if not content.get("success"):
        error = content.get("error", "Gagal membaca file PDF")
        logger.warning("analyze_document: gagal baca PDF file=%s error=%s", file_path, error)
        return {
            "success": False,
            "error": error,
            "full_text": "",
            "total_pages": 0,
            "authenticity": analyze_authenticity({"success": False, "error": error}, ""),
            "ocr_summary": _build_ocr_summary_pymupdf(0, _OCR_ENABLED),
        }

    pages = content["pages"]  # list[{"page": N, "text": str}]
    total_pages = len(pages)
    meta = content.get("metadata") or {"success": False, "error": "Metadata tidak tersedia"}

    # Phase B: Routing
    route = compute_route(pages, _OCR_MIN_TEXT_CHARS)
    pages_no_text = sum(len(p.get("text", "").strip()) < _OCR_MIN_TEXT_CHARS for p in pages)
    logger.info(
        "ocr_routing file=%s route=%s total_pages=%d no_text=%d model=%s",
        file_path, route, total_pages, pages_no_text, _OCR_MODEL,
    )

    # Phase C: Dispatch ke jalur yang sesuai
    if route == "pymupdf":
        full_text = content["full_text"]
        ocr_summary = _build_ocr_summary_pymupdf(total_pages, _OCR_ENABLED)
    elif not ocr_service.enabled:
        # FR-5: route=ocr tapi OCR disabled/no api_key → fallback ke PyMuPDF apa adanya.
        # Tidak render, tidak panggil Gemini. ocr_summary.enabled=false + route=ocr
        # supaya postprocess set requires_manual_review + review_reasons "ocr_unavailable".
        logger.warning(
            "ocr_unavailable file=%s reason=%s route=ocr total_pages=%d",
            file_path,
            "disabled" if _OCR_ENABLED is False else "no_api_key",
            total_pages,
        )
        full_text = content["full_text"]
        ocr_summary = _build_ocr_summary_ocr(
            total_pages=total_pages,
            outputs=[],
            pages_skipped=0,
            enabled=False,
            model=_OCR_MODEL,
        )
    else:
        # Jalur OCR: render + kirim semua halaman ke Gemini (cap MAX_PAGES)
        pages_to_ocr = pages[:_OCR_MAX_PAGES]
        pages_skipped_list = pages[_OCR_MAX_PAGES:]

        # Render off-thread agar tidak blocking event loop.
        rendered = await asyncio.to_thread(
            render_pages_png,
            file_path,
            [p["page"] for p in pages_to_ocr],
            _OCR_RENDER_DPI,
        )

        ocr_inputs: list[PageOcrInput] = []
        render_failed_outputs: list[PageOcrOutput] = []
        for page_number, image_bytes in rendered:
            if image_bytes is None:
                render_failed_outputs.append(
                    PageOcrOutput(
                        page_number=page_number,
                        text="",
                        duration_ms=0,
                        error="render_failed",
                    )
                )
            else:
                ocr_inputs.append(
                    PageOcrInput(page_number=page_number, image_bytes=image_bytes)
                )

        ocr_results = await ocr_service.ocr_pages(ocr_inputs)
        ocr_outputs = ocr_results + render_failed_outputs
        ocr_outputs.sort(key=lambda o: o.page_number)

        # Gabung teks urut nomor halaman; halaman skip → teks kosong
        text_by_page: dict[int, str] = {o.page_number: o.text for o in ocr_outputs}
        for p in pages_skipped_list:
            text_by_page[p["page"]] = ""

        full_text = "\n\n".join(
            f"=== HALAMAN {p['page']} ===\n{text_by_page.get(p['page'], '')}"
            for p in pages
        )

        ocr_summary = _build_ocr_summary_ocr(
            total_pages=total_pages,
            outputs=ocr_outputs,
            pages_skipped=len(pages_skipped_list),
            enabled=_OCR_ENABLED,
            model=_OCR_MODEL,
        )

        logger.info(
            "ocr_result file=%s success=%d failed=%d skipped=%d duration_ms=%d",
            file_path,
            ocr_summary["pages_ocr_success"],
            ocr_summary["pages_ocr_failed"],
            ocr_summary["pages_skipped"],
            ocr_summary["ocr_duration_ms"],
        )

    # Phase D: Authenticity (sama untuk semua jalur)
    authenticity = analyze_authenticity(meta, full_text)

    return {
        "success": True,
        "file_path": file_path,
        "total_pages": total_pages,
        "full_text": full_text,
        "authenticity": authenticity,
        "ocr_summary": ocr_summary,
    }
