"""Unified SSE (Server-Sent Events) utility for LifeQuery.

Provides consistent SSE event creation across all routers.
"""

import json
from typing import Any, Union

from sse_starlette import JSONServerSentEvent, ServerSentEvent


def create_sse_event(data: Union[dict[str, Any], str]) -> ServerSentEvent:
    """Create an SSE event from a dict or string.

    Args:
        data: Either a dict (will be JSON-serialized) or a string
              (for special markers like '[DONE]')

    Returns:
        ServerSentEvent properly formatted for SSE streaming
    """
    if isinstance(data, str):
        return ServerSentEvent(data=data)
    return JSONServerSentEvent(data=data)


def create_error_event(message: str) -> ServerSentEvent:
    """Create a standardized error event.

    Args:
        message: Error message to send

    Returns:
        ServerSentEvent with error type
    """
    return create_sse_event({"type": "error", "message": message})


def create_progress_event(stage: str, message: str) -> ServerSentEvent:
    """Create a standardized progress event.

    Args:
        stage: Current operation stage (e.g., 'ingest', 'chunk', 'embed')
        message: Human-readable progress message

    Returns:
        ServerSentEvent with progress type
    """
    return create_sse_event({"type": "progress", "stage": stage, "message": message})


def create_done_event(**kwargs: Any) -> ServerSentEvent:
    """Create a standardized done event.

    Args:
        **kwargs: Any fields to include in the done event
                (e.g., messages_added, chunks_created)

    Returns:
        ServerSentEvent with done type
    """
    event_data = {"type": "done", **kwargs}
    return create_sse_event(event_data)


def create_token_event(content: str) -> ServerSentEvent:
    """Create a token event for streaming chat responses.

    Args:
        content: Token content string

    Returns:
        ServerSentEvent with token type
    """
    return create_sse_event({"type": "token", "content": content})


def create_citations_event(citations: list[dict[str, Any]]) -> ServerSentEvent:
    """Create a citations event for chat completion.

    Args:
        citations: List of citation dicts with chat_name, date_range, participants

    Returns:
        ServerSentEvent with citations type
    """
    return create_sse_event({"type": "citations", "citations": citations})
