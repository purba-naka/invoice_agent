from google.adk.agents import LlmAgent

from ..tools.combined import analyze_document
from .prompts import RECEIPT_PROMPT

receipt_agent = LlmAgent(
    model="gemini-2.5-flash",
    name="receipt_agent",
    description="Verifikasi dan ekstraksi data receipt dari dokumen PDF. Input: file_path.",
    instruction=RECEIPT_PROMPT,
    tools=[analyze_document],
)
