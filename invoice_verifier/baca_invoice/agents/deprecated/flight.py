from google.adk.agents import LlmAgent

from ..tools.combined import analyze_document
from .prompts import FLIGHT_SINGLE

flight_agent = LlmAgent(
    model="gemini-2.5-flash",
    name="flight_ticket_agent",
    description="Verifikasi dan ekstraksi data tiket pesawat dari PDF. Input: file_path.",
    instruction=FLIGHT_SINGLE,
    tools=[analyze_document],
)
