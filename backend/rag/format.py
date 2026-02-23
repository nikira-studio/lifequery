"""RAG format module - citation formatting and output formatting.

This module handles:
- Formatting citations from retrieved chunks
- Converting chunk data to user-friendly formats
"""

from datetime import datetime
from typing import Any


def format_debug(messages: list[dict], user_name: str, current_date: str) -> dict:
    """Format debug info showing full messages sent to LLM.

    Args:
        messages: List of message dicts with 'role' and 'content'
        user_name: The user's name for placeholder replacement
        current_date: Today's date

    Returns:
        Debug dict with messages and metadata
    """

    # Replace placeholders for display
    def replace_placeholders(text: str) -> str:
        text = text.replace("{user_name}", user_name)
        text = text.replace("{current_date}", current_date)
        text = text.replace("{context_text}", "[context would be here]")
        return text

    return {
        "type": "debug",
        "messages": [
            {
                "role": msg.get("role", "unknown"),
                "content": replace_placeholders(msg.get("content", "")),
            }
            for msg in messages
        ],
        "user_name": user_name,
        "current_date": current_date,
    }


def fmt_date(timestamp: int) -> str:
    """Format a Unix timestamp to a readable date string.

    Args:
        timestamp: Unix timestamp in seconds

    Returns:
        Formatted date string (e.g., "2024-01-15")
    """
    if timestamp == 0:
        return "Unknown"
    dt = datetime.utcfromtimestamp(timestamp)
    return dt.strftime("%Y-%m-%d")


def format_citation(chunk: Any) -> dict[str, Any]:
    """Format a single chunk as a citation dict.

    Args:
        chunk: RetrievedChunk object

    Returns:
        Citation dict with chat_name, date_range, and participants
    """
    date_range = f"{fmt_date(chunk.timestamp_start)}–{fmt_date(chunk.timestamp_end)}"

    # Handle participants - could be list or string
    participants = chunk.participants
    if isinstance(participants, str):
        import json

        try:
            participants = json.loads(participants)
        except (json.JSONDecodeError, TypeError):
            participants = []

    return {
        "chat_name": chunk.chat_name or "Unknown",
        "date_range": date_range,
        "participants": participants,
        "content": chunk.content,
    }


def format_citations(chunks: list[Any]) -> list[dict[str, Any]]:
    """Format multiple chunks as citation list.

    Args:
        chunks: List of RetrievedChunk objects

    Returns:
        List of citation dicts
    """
    return [format_citation(chunk) for chunk in chunks]


def format_error(message: str) -> dict[str, str]:
    """Format an error event.

    Args:
        message: Error message

    Returns:
        Error event dict
    """
    return {"type": "error", "message": message}


def format_token(content: str) -> dict[str, str]:
    """Format a token event.

    Args:
        content: Token content string

    Returns:
        Token event dict
    """
    return {"type": "token", "content": content}


def format_citations_event(citations: list[dict[str, Any]]) -> dict[str, Any]:
    """Format a citations event.

    Args:
        citations: List of citation dicts

    Returns:
        Citations event dict
    """
    return {"type": "citations", "citations": citations}
