from google.adk.agents import LlmAgent

from ..tools.combined import analyze_document
from .prompts import HOTEL_SINGLE

hotel_agent = LlmAgent(
    model="gemini-2.5-flash",
    name="hotel_invoice_agent",
    description="Verifikasi dan ekstraksi data invoice hotel dari PDF. Input: file_path.",
    instruction=HOTEL_SINGLE,
    tools=[analyze_document],
)
