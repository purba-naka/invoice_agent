from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import fitz


def _parse_pdf_date(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        cleaned = raw.replace("D:", "").strip()[:14]
        return datetime.strptime(cleaned, "%Y%m%d%H%M%S").isoformat()
    except Exception:
        return raw or None


def _compute_modification_info(
    creation_date: str | None, mod_date: str | None
) -> tuple[bool, int | None]:
    if not (creation_date and mod_date and creation_date != mod_date):
        return False, None
    try:
        gap_seconds = (
            datetime.fromisoformat(mod_date) - datetime.fromisoformat(creation_date)
        ).total_seconds()
    except Exception:
        return False, None
    if gap_seconds <= 300:
        return False, None
    return True, int(gap_seconds // 86400)


def render_pages_png(
    file_path: str, page_numbers: list[int], dpi: int = 200
) -> list[tuple[int, bytes | None]]:
    """Render banyak halaman dalam satu kali buka file.

    Returns:
        list of (page_number, png_bytes | None). `None` jika halaman gagal dirender.
        Tidak pernah raise — error per-halaman dikemas via None.
    """
    results: list[tuple[int, bytes | None]] = []
    try:
        doc = fitz.open(file_path)
    except Exception:
        return [(p, None) for p in page_numbers]
    try:
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        total = len(doc)
        for page_number in page_numbers:
            idx = page_number - 1
            if idx < 0 or idx >= total:
                results.append((page_number, None))
                continue
            try:
                pixmap = doc[idx].get_pixmap(matrix=matrix)
                results.append((page_number, pixmap.tobytes("png")))
            except Exception:
                results.append((page_number, None))
    finally:
        doc.close()
    return results


def render_page_png(file_path: str, page_number: int, dpi: int = 200) -> bytes:
    """Render satu halaman PDF menjadi PNG bytes in-memory.

    Args:
        file_path: Path absolut ke file PDF.
        page_number: Nomor halaman 1-based.
        dpi: Resolusi render (default 200).

    Returns:
        PNG bytes dari halaman tersebut.

    Raises:
        ValueError: Jika page_number di luar rentang.
        Exception: Jika file tidak bisa dibuka atau halaman tidak bisa dirender.
    """
    doc = fitz.open(file_path)
    try:
        idx = page_number - 1
        if idx < 0 or idx >= len(doc):
            raise ValueError(f"page_number {page_number} out of range (1..{len(doc)})")
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        pixmap = doc[idx].get_pixmap(matrix=matrix)
        return pixmap.tobytes("png")
    finally:
        doc.close()


def read_pdf(file_path: str) -> dict[str, Any]:
    """Baca seluruh teks dan metadata PDF dalam satu kali open file.

    Returns:
        dict dengan key:
          success, file_path, total_pages, full_text, pages, metadata, error.
        `metadata` berisi: title, author, creator, producer, creation_date,
        modification_date, was_modified, modification_gap_days.
    """
    if not os.path.exists(file_path):
        return {"success": False, "error": f"File tidak ditemukan: {file_path}"}
    try:
        doc = fitz.open(file_path)
        try:
            raw_meta = doc.metadata
            pages = [{"page": i + 1, "text": doc[i].get_text()} for i in range(len(doc))]
        finally:
            doc.close()
    except Exception as exc:
        return {"success": False, "error": str(exc), "file_path": file_path}

    full_text = "\n\n".join(f"=== HALAMAN {p['page']} ===\n{p['text']}" for p in pages)
    creation_date = _parse_pdf_date(raw_meta.get("creationDate"))
    mod_date = _parse_pdf_date(raw_meta.get("modDate"))
    was_modified, modification_gap_days = _compute_modification_info(creation_date, mod_date)

    metadata = {
        "success": True,
        "title": raw_meta.get("title", ""),
        "author": raw_meta.get("author", ""),
        "creator": raw_meta.get("creator", ""),
        "producer": raw_meta.get("producer", ""),
        "creation_date": creation_date,
        "modification_date": mod_date,
        "was_modified": was_modified,
        "modification_gap_days": modification_gap_days,
    }
    return {
        "success": True,
        "file_path": file_path,
        "total_pages": len(pages),
        "full_text": full_text,
        "pages": pages,
        "metadata": metadata,
    }
