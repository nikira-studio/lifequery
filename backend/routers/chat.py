"""Chat router - POST /api/chat endpoint."""

from typing import AsyncGenerator

from fastapi import APIRouter
from pydantic import BaseModel, Field
from rag.pipeline import rag_stream_query
from sse_starlette import EventSourceResponse
from sse_starlette.sse import ServerSentEvent
from utils.logger import get_logger
from utils.sse import create_error_event, create_sse_event
from utils.validation import extract_query_from_messages, validate_chat_messages

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    """Chat request with conversation history."""

    messages: list[dict] = Field(
        ..., description="List of messages in the conversation"
    )


async def chat_generator(
    messages: list[dict],
) -> AsyncGenerator[ServerSentEvent, None]:
    """SSE generator for chat operation.

    Args:
        messages: List of message dicts with 'role' and 'content' keys

    Yields:
        ServerSentEvent objects for tokens, citations, and errors
    """
    # Validate messages using shared validation utility
    is_valid, error_message = validate_chat_messages(messages)
    if not is_valid:
        yield create_error_event(error_message)
        return

    # Extract query and history using shared utility
    query_text, conversation_history = extract_query_from_messages(messages)

    logger.info(f"Processing chat request: {query_text[:100]}...")

    try:
        # Stream RAG query
        async for event in rag_stream_query(query_text, conversation_history):
            yield create_sse_event(event)

        # Send completion marker
        yield create_sse_event("[DONE]")

        logger.info("Chat request completed successfully")

    except Exception as e:
        logger.error(f"Error in chat generator: {e}", exc_info=True)
        from utils.error_beautifier import beautify_error

        yield create_error_event(beautify_error(e))


@router.post("/chat")
async def chat(request: ChatRequest):
    """Chat with LifeQuery using RAG.

    Accepts a list of messages (conversation history) and returns a streaming response
    with tokens and citations from retrieved Telegram conversations.

    Request:
        {
            "messages": [
                {"role": "user", "content": "What was I stressed about last November?"},
                {"role": "assistant", "content": "Based on your conversations..."},
                {"role": "user", "content": "Tell me more about that."}
            ]
        }

    Response (SSE stream):
        data: {"type": "token", "content": "Based"}
        data: {"type": "token", "content": " on"}
        ...
        data: {"type": "citations", "citations": [...]}
        data: [DONE]

    Events:
        - token: Individual tokens from the LLM response
        - citations: List of citations with chat_name, date_range, participants
        - error: Error message if something goes wrong (sent as SSE event, HTTP status remains 200)
        - [DONE]: Stream completion marker

    Note: Errors are delivered as SSE 'error' events rather than HTTP status codes,
    since this is a streaming endpoint. The HTTP response will always be 200 OK.
    """
    logger.info(f"Received chat request with {len(request.messages)} messages")

    return EventSourceResponse(
        chat_generator(request.messages),
        headers={"X-Accel-Buffering": "no"},
    )
