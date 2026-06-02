"""GeminiOcrService: OCR halaman PDF via Gemini Vision.

Kontrak:
- Tidak pernah raise ke caller — semua error per-halaman dikemas di PageOcrOutput.error.
- Tidak menyimpan image bytes ke disk.
- Thread-safe dan aman di-share lintas request (stateless di luar config init).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_OCR_PROMPT = (
    "Anda adalah OCR engine. Baca semua teks yang terlihat di gambar ini, "
    "pertahankan urutan baris dan paragraf. "
    "Keluarkan hanya teks mentah tanpa komentar, tanpa markdown, tanpa interpretasi. "
    "Jika tidak ada teks, balas dengan string kosong."
)


@dataclass
class PageOcrInput:
    page_number: int
    image_bytes: bytes
    mime_type: str = "image/png"


@dataclass
class PageOcrOutput:
    page_number: int
    text: str
    duration_ms: int
    error: str | None = None


class GeminiOcrService:
    """Async OCR service berbasis Gemini Vision.

    Args:
        api_key: Google API key. Jika None, service.enabled = False dan
                 ocr_pages() mengembalikan error "ocr_disabled" tanpa network call.
        model: Nama model Gemini (default "gemini-2.5-flash").
        concurrency: Maksimal panggilan Gemini bersamaan per batch.
        timeout_seconds: Timeout per halaman OCR dalam detik.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = "gemini-2.5-flash",
        concurrency: int = 3,
        timeout_seconds: float = 30.0,
        enabled: bool = True,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._enabled_flag = enabled
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client: object | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._api_key) and self._enabled_flag

    def _get_client(self) -> object:
        """Lazy-init genai Client (hindari import cost saat disabled)."""
        if self._client is None:
            from google import genai  # noqa: PLC0415

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def ocr_pages(self, pages: list[PageOcrInput]) -> list[PageOcrOutput]:
        """OCR semua halaman secara paralel dengan concurrency cap.

        Tidak pernah raise. Semua kegagalan dilaporkan via PageOcrOutput.error.
        """
        if not self.enabled:
            return [
                PageOcrOutput(page_number=p.page_number, text="", duration_ms=0, error="ocr_disabled")
                for p in pages
            ]
        tasks = [self._ocr_one_page(p) for p in pages]
        return list(await asyncio.gather(*tasks))

    async def _ocr_one_page(self, page: PageOcrInput) -> PageOcrOutput:
        async with self._semaphore:
            start = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self._call_gemini(page),
                    timeout=self._timeout,
                )
                duration_ms = int((time.monotonic() - start) * 1000)
                return PageOcrOutput(page_number=page.page_number, text=result, duration_ms=duration_ms)
            except asyncio.TimeoutError:
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.warning("ocr_timeout page=%d duration_ms=%d", page.page_number, duration_ms)
                return PageOcrOutput(
                    page_number=page.page_number, text="", duration_ms=duration_ms, error="timeout"
                )
            except Exception as exc:  # noqa: BLE001
                duration_ms = int((time.monotonic() - start) * 1000)
                error_str = str(exc)
                # Deteksi rate limit (429) dari pesan error
                if "429" in error_str or "rate" in error_str.lower():
                    error_code = "rate_limited"
                else:
                    error_code = f"api_error: {error_str[:120]}"
                logger.warning("ocr_error page=%d error=%s", page.page_number, error_code)
                return PageOcrOutput(
                    page_number=page.page_number, text="", duration_ms=duration_ms, error=error_code
                )

    async def _call_gemini(self, page: PageOcrInput) -> str:
        from google.genai import types  # noqa: PLC0415

        client = self._get_client()
        response = await client.aio.models.generate_content(  # type: ignore[union-attr]
            model=self._model,
            contents=types.Content(
                parts=[
                    types.Part(
                        inline_data=types.Blob(data=page.image_bytes, mime_type=page.mime_type)
                    ),
                    types.Part(text=_OCR_PROMPT),
                ]
            ),
            config=types.GenerateContentConfig(temperature=0.0),
        )
        return (response.text or "").strip()


# Singleton global — dikonfigurasi dari env saat module load.
# combined.py mengimpor instance ini.
_OCR_ENABLED_ENV = os.getenv("OCR_ENABLED", "1").lower() not in ("0", "false", "no")

ocr_service = GeminiOcrService(
    api_key=os.getenv("GOOGLE_API_KEY") or None,
    model=os.getenv("OCR_MODEL", "gemini-2.5-flash"),
    concurrency=int(os.getenv("OCR_CONCURRENCY", "3")),
    timeout_seconds=float(os.getenv("OCR_TIMEOUT_SECONDS", "30")),
    enabled=_OCR_ENABLED_ENV,
)
