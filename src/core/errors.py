class AgentError(Exception):
    """Base class for expected agent failures."""


class ProviderError(AgentError):
    pass


class ToolError(AgentError):
    pass

