"""Validation utilities for LifeQuery routers.

Provides common validation functions to reduce duplication across routers.
"""


def validate_chat_messages(messages: list[dict]) -> tuple[bool, str | None]:
    """Validate chat messages list.

    Args:
        messages: List of message dicts with 'role' and 'content' keys

    Returns:
        Tuple of (is_valid, error_message). If valid, error_message is None.
    """
    if not messages:
        return False, "No messages provided"

    last_message = messages[-1]
    if last_message.get("role") != "user":
        return False, "Last message must be from user"

    query_text = last_message.get("content", "")
    if not query_text:
        return False, "Query cannot be empty"

    return True, None


def extract_query_from_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Extract query text and conversation history from messages.

    Args:
        messages: List of message dicts with 'role' and 'content' keys

    Returns:
        Tuple of (query_text, conversation_history). Query is the last user message,
        history is all messages except the last one.
    """
    query_text = messages[-1].get("content", "")
    conversation_history = messages[:-1] if len(messages) > 1 else []
    return query_text, conversation_history


def validate_phone(phone: str) -> tuple[bool, str | None]:
    """Validate phone number format.

    Args:
        phone: Phone number string

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not phone:
        return False, "Phone number is required"

    # Basic validation - should start with + and have digits
    if not phone.startswith("+"):
        return False, "Phone number must include country code (start with +)"

    # Remove + and check remaining is all digits
    digits = phone[1:]
    if not digits.isdigit():
        return False, "Phone number must contain only digits after country code"

    if len(digits) < 7:
        return False, "Phone number is too short"

    return True, None


def validate_code(code: str) -> tuple[bool, str | None]:
    """Validate verification code format.

    Args:
        code: Verification code string

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not code:
        return False, "Verification code is required"

    if not code.isdigit():
        return False, "Verification code must contain only digits"

    if len(code) < 5 or len(code) > 7:
        return False, "Verification code must be 5-7 digits"

    return True, None
