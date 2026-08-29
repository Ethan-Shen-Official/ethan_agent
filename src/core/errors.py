class AgentError(Exception):
    """Base class for expected agent failures."""


class ProviderError(AgentError):
    pass


class SessionError(AgentError):
    """Raised when a persisted session cannot be read or written safely."""


class ToolError(AgentError):
    """Structured failure raised by a tool implementation."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "tool_error",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.message = str(message)
        self.code = str(code)
        self.retryable = bool(retryable)

    def __str__(self) -> str:
        return self.message


def format_tool_error(error: BaseException) -> str:
    """Return stable text exposed to the model for a tool failure."""
    if isinstance(error, ToolError) and error.code and error.code != "tool_error":
        return f"{error.code}: {error.message}"
    message = str(error).strip()
    return message or error.__class__.__name__
