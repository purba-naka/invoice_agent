from google.adk.agents import LlmAgent

from ..tools.combined import analyze_document
from .prompts import DOCUMENT_AGENT_PROMPT

document_agent = LlmAgent(
    model="gemini-2.5-flash",
    name="document_verification_agent",
    description="Membaca file PDF, mendeteksi keaslian, dan mengekstraksi data perjalanan terstruktur. Input: file_path.",
    instruction=DOCUMENT_AGENT_PROMPT,
    tools=[analyze_document],
)
