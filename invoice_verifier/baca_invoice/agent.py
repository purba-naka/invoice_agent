from .agents.document import document_agent

# Expose the single unified agent for ADK CLI
root_agent = document_agent

__all__ = ["root_agent", "document_agent"]
