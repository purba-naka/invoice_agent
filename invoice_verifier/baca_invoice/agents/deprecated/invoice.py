from google.adk.agents import LlmAgent

from ..tools.combined import analyze_document
from .prompts import INVOICE_PROMPT

invoice_agent = LlmAgent(
    model="gemini-2.5-flash",
    name="invoice_agent",
    description="Verifikasi dan ekstraksi data invoice dari dokumen PDF. Input: file_path.",
    instruction=INVOICE_PROMPT,
    tools=[analyze_document],
)
