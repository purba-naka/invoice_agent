from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .authenticity import DocumentAuthenticity


class OcrSummary(BaseModel):
    """Ringkasan jalur ekstraksi per dokumen. Selalu hadir di response."""

    enabled: bool = Field(default=False, description="True jika OCR diaktifkan saat proses.")
    route: Literal["pymupdf", "ocr"] = Field(
        default="pymupdf",
        description="Jalur ekstraksi yang dipilih: 'pymupdf' (text-layer) atau 'ocr' (Gemini Vision).",
    )
    total_pages: int = Field(default=0, description="Total halaman PDF.")
    pages_text_layer: int = Field(
        default=0,
        description="Halaman yang terhitung punya text-layer (digunakan untuk keputusan routing).",
    )
    pages_ocr_success: int = Field(default=0, description="Halaman berhasil di-OCR. 0 jika route=pymupdf.")
    pages_ocr_failed: int = Field(default=0, description="Halaman gagal OCR. 0 jika route=pymupdf.")
    pages_skipped: int = Field(
        default=0,
        description="Halaman di-skip karena melebihi OCR_MAX_PAGES. 0 jika route=pymupdf.",
    )
    ocr_duration_ms: int = Field(default=0, description="Durasi OCR terlama (ms). 0 jika route=pymupdf.")
    model: str = Field(default="", description="Model Gemini yang dipakai. '' jika route=pymupdf.")


class InvoiceLineItem(BaseModel):
    description: str = Field(default="-", description="Deskripsi item tagihan.")
    quantity: float = Field(default=0.0, description="Jumlah/quantity item.")
    unit_price: float = Field(default=0.0, description="Harga satuan sebelum diskon/pajak.")
    subtotal: float = Field(default=0.0, description="Subtotal = quantity x unit_price.")


class ReceiptItem(BaseModel):
    description: str = Field(default="-", description="Deskripsi item yang dibeli.")
    quantity: float = Field(default=0.0, description="Jumlah/quantity item.")
    price: float = Field(default=0.0, description="Harga total untuk item ini.")


class AddonItem(BaseModel):
    description: str = Field(default="-", description="Deskripsi addon (mis. bagasi, meal, asuransi).")
    price: float = Field(default=0.0, description="Harga addon.")


class TravelDocumentResult(BaseModel):
    # === Klasifikasi ===
    doc_type: Literal["invoice", "receipt", "unknown"] = Field(
        default="unknown",
        description=(
            "Kategori dokumen: 'invoice' (tagihan/faktur dengan invoice_number, "
            "vendor, line item tagihan, payment terms), 'receipt' (bukti bayar "
            "dengan receipt_number, payment_status paid/lunas, merchant), atau "
            "'unknown' (dokumen lain di luar kedua kategori)."
        ),
    )
    document_subtype: Literal["hotel", "flight", "unknown"] = Field(
        default="unknown",
        description=(
            "Subtype berdasarkan isi: 'hotel' (penginapan/kamar/check-in), "
            "'flight' (penerbangan/airline/rute), atau 'unknown'. Dievaluasi "
            "terpisah dari doc_type kecuali doc_type='unknown'."
        ),
    )

    # === Blok INVOICE ===
    invoice_number: str = Field(default="-", description="Nomor invoice/faktur.")
    issue_date: str = Field(default="-", description="Tanggal terbit invoice.")
    due_date: str = Field(default="-", description="Tanggal jatuh tempo pembayaran.")
    vendor_name: str = Field(default="-", description="Nama vendor/penjual (pihak yang menagih).")
    vendor_address: str = Field(default="-", description="Alamat vendor.")
    vendor_npwp: str = Field(default="-", description="NPWP vendor.")
    vendor_phone: str = Field(default="-", description="Telepon vendor.")
    vendor_email: str = Field(default="-", description="Email vendor.")
    buyer_name: str = Field(default="-", description="Nama buyer/pembeli (pihak yang ditagih).")
    buyer_address: str = Field(default="-", description="Alamat buyer.")
    buyer_npwp: str = Field(default="-", description="NPWP buyer.")
    line_items: list[InvoiceLineItem] = Field(
        default_factory=list,
        description="Daftar baris tagihan dari invoice.",
    )
    payment_terms: str = Field(default="-", description="Termin pembayaran (mis. Net 30, COD).")

    # === Blok RECEIPT ===
    receipt_number: str = Field(default="-", description="Nomor kwitansi/struk/transaksi.")
    transaction_date: str = Field(default="-", description="Tanggal transaksi.")
    payment_date: str = Field(default="-", description="Tanggal pembayaran (jika berbeda dari transaksi).")
    merchant_name: str = Field(default="-", description="Nama merchant/penerima pembayaran.")
    merchant_address: str = Field(default="-", description="Alamat merchant.")
    merchant_phone: str = Field(default="-", description="Telepon merchant.")
    payer_name: str = Field(default="-", description="Nama pembayar.")
    payer_email: str = Field(default="-", description="Email pembayar.")
    payer_phone: str = Field(default="-", description="Telepon pembayar.")
    items_purchased: list[ReceiptItem] = Field(
        default_factory=list,
        description="Daftar item yang dibeli pada receipt.",
    )
    payment_status: str = Field(
        default="-",
        description="Status pembayaran (paid/lunas/settled/success/pending).",
    )

    # === Blok HOTEL ===
    order_id: str = Field(default="-", description="ID order booking hotel.")
    order_detail_id: str = Field(default="-", description="ID detail order/booking line.")
    booking_date: str = Field(default="-", description="Tanggal booking dibuat.")
    booker_name: str = Field(default="-", description="Nama yang melakukan booking.")
    booker_email: str = Field(default="-", description="Email booker.")
    booker_phone: str = Field(default="-", description="Telepon booker.")
    hotel_name: str = Field(default="-", description="Nama hotel.")
    hotel_address: str = Field(default="-", description="Alamat hotel.")
    hotel_city: str = Field(default="-", description="Kota hotel.")
    hotel_phone: str = Field(default="-", description="Telepon hotel.")
    room_type: str = Field(default="-", description="Tipe kamar (Deluxe, Suite, dll).")
    total_rooms: int = Field(default=0, description="Jumlah kamar yang dipesan.")
    room_capacity: str = Field(default="-", description="Kapasitas kamar (mis. '2 dewasa, 1 anak').")
    check_in_date: str = Field(default="-", description="Tanggal check-in.")
    check_in_time: str = Field(default="-", description="Jam check-in.")
    check_out_date: str = Field(default="-", description="Tanggal check-out.")
    check_out_time: str = Field(default="-", description="Jam check-out.")
    total_nights: int = Field(default=0, description="Total malam menginap.")
    breakfast_included: bool = Field(default=False, description="True jika sarapan termasuk.")
    facilities: str = Field(default="-", description="Fasilitas yang disertakan.")
    special_requests: str = Field(default="-", description="Permintaan khusus tamu.")

    # === Blok FLIGHT ===
    po_number: str = Field(default="-", description="Nomor PO/booking flight.")
    transaction_status: str = Field(default="-", description="Status transaksi tiket (issued/pending/cancelled).")
    traveler_name: str = Field(default="-", description="Nama penumpang.")
    traveler_email: str = Field(default="-", description="Email penumpang.")
    traveler_phone: str = Field(default="-", description="Telepon penumpang.")
    airline: str = Field(default="-", description="Maskapai penerbangan.")
    route_from: str = Field(default="-", description="Kota/bandara asal.")
    route_to: str = Field(default="-", description="Kota/bandara tujuan.")
    flight_date: str = Field(default="-", description="Tanggal penerbangan.")
    seat_class: str = Field(default="-", description="Kelas kursi (Economy/Business/First).")
    passenger_type: str = Field(default="-", description="Tipe penumpang (adult/child/infant).")
    ticket_price: float = Field(default=0.0, description="Harga tiket dasar.")
    addons: list[AddonItem] = Field(default_factory=list, description="Addon penerbangan (bagasi, meal, dll).")

    # === COMMON (semua dokumen) ===
    subtotal: float = Field(default=0.0, description="Subtotal sebelum diskon/pajak/biaya.")
    discount: float = Field(default=0.0, description="Total diskon.")
    tax: float = Field(default=0.0, description="Pajak (PPN, VAT, dll).")
    service_fee: float = Field(default=0.0, description="Biaya layanan/service.")
    total_payment: float = Field(default=0.0, description="Total akhir yang dibayarkan.")
    payment_method: str = Field(default="-", description="Metode pembayaran (transfer, kartu, e-wallet).")
    payment_date_time: str = Field(default="-", description="Tanggal & jam pembayaran.")
    currency: str = Field(default="IDR", description="Kode mata uang (IDR, USD, dll).")

    # === Provider (platform/agent pemesan, mis. Traveloka, Tiket.com) ===
    provider: str = Field(default="-", description="Nama platform/aplikasi pemesan.")
    provider_company: str = Field(default="-", description="Badan hukum provider.")
    provider_address: str = Field(default="-", description="Alamat provider.")
    provider_npwp: str = Field(default="-", description="NPWP provider.")

    # === Meta ===
    authenticity: DocumentAuthenticity = Field(
        default_factory=DocumentAuthenticity,
        description="Hasil analisis keaslian dari tool. Disalin apa adanya, tidak boleh diubah.",
    )
    extraction_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence ekstraksi (0.0-1.0). Diisi 0.0 oleh agent; dihitung deterministik di post-process.",
    )
    requires_manual_review: bool = Field(
        default=False,
        description="True jika perlu review manusia. Diisi false oleh agent; dihitung deterministik di post-process.",
    )
    review_reasons: list[str] = Field(
        default_factory=list,
        description="Daftar alasan review. Diisi [] oleh agent; diisi deterministik di post-process.",
    )
    summary: str = Field(
        default="-",
        description="Ringkasan 1-2 kalimat: pihak utama, tanggal/rute/kamar, total, verdict authenticity.",
    )
    ocr_summary: OcrSummary = Field(
        default_factory=lambda: OcrSummary(
            enabled=False,
            route="pymupdf",
            total_pages=0,
            pages_text_layer=0,
            pages_ocr_success=0,
            pages_ocr_failed=0,
            pages_skipped=0,
            ocr_duration_ms=0,
            model="",
        ),
        description="Ringkasan jalur ekstraksi (pymupdf vs gemini OCR). Selalu hadir.",
    )

    @field_validator(
        "doc_type",
        "document_subtype",
        "invoice_number",
        "issue_date",
        "due_date",
        "vendor_name",
        "vendor_address",
        "vendor_npwp",
        "vendor_phone",
        "vendor_email",
        "buyer_name",
        "buyer_address",
        "buyer_npwp",
        "payment_terms",
        "receipt_number",
        "transaction_date",
        "payment_date",
        "merchant_name",
        "merchant_address",
        "merchant_phone",
        "payer_name",
        "payer_email",
        "payer_phone",
        "payment_status",
        "order_id",
        "order_detail_id",
        "booking_date",
        "booker_name",
        "booker_email",
        "booker_phone",
        "hotel_name",
        "hotel_address",
        "hotel_city",
        "hotel_phone",
        "room_type",
        "room_capacity",
        "check_in_date",
        "check_in_time",
        "check_out_date",
        "check_out_time",
        "facilities",
        "special_requests",
        "po_number",
        "transaction_status",
        "traveler_name",
        "traveler_email",
        "traveler_phone",
        "airline",
        "route_from",
        "route_to",
        "flight_date",
        "seat_class",
        "passenger_type",
        "payment_method",
        "payment_date_time",
        "currency",
        "provider",
        "provider_company",
        "provider_address",
        "provider_npwp",
        "summary",
        mode="before",
    )
    @classmethod
    def normalize_string_fields(cls, value: Any) -> str:
        if value is None:
            return "-"
        if isinstance(value, list):
            return ", ".join(str(item) for item in value if item not in (None, ""))
        if isinstance(value, dict):
            return str(value)
        return str(value)
