"""OpenAI-compatible endpoint for /v1/chat/completions."""

import logging
import secrets
import time
from dataclasses import replace
from datetime import datetime
from typing import Any, AsyncGenerator

from config import settings
from fastapi import APIRouter, HTTPException, Request
from rag.pipeline import rag_stream_query
from schemas import (
    Citation,
    OpenAIChatRequest,
    OpenAIChatResponse,
    OpenAIChoice,
    OpenAIDelta,
    OpenAIMessage,
    OpenAIUsage,
)
from sse_starlette import EventSourceResponse
from sse_starlette.sse import ServerSentEvent
from utils.sse import create_sse_event
from utils.validation import extract_query_from_messages, validate_chat_messages

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["openai-compatible"])


def _generate_chat_id() -> str:
    """Generate an OpenAI-style chat completion ID."""
    timestamp = datetime.now().timestamp()
    random_suffix = secrets.token_hex(4)
    return f"chatcmpl-{int(timestamp)}-{random_suffix}"


def _to_openai_sse_event(data: dict) -> ServerSentEvent:
    """Convert a dictionary to an OpenAI SSE event.

    Args:
        data: Dictionary with OpenAI format

    Returns:
        ServerSentEvent with properly formatted JSON data
    """
    import json

    json_data = json.dumps(data)
    return ServerSentEvent(data=json_data)


def _to_openai_error(
    message: str, error_type: str = "invalid_request_error"
) -> ServerSentEvent:
    """Create an OpenAI-style error event.

    Args:
        message: Error message
        error_type: OpenAI error type

    Returns:
        ServerSentEvent with OpenAI error format
    """
    return _to_openai_sse_event(
        {
            "error": {
                "message": message,
                "type": error_type,
            }
        }
    )


async def chat_completion_streaming_generator(
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    rag: bool | None = None,
    thinking: bool | None = None,
) -> AsyncGenerator[ServerSentEvent, None]:
    """SSE generator for streaming OpenAI chat completions.

    Args:
        messages: List of message dicts with 'role' and 'content'
        temperature: Override temperature (optional)
        max_tokens: Override max_tokens (optional)

    Yields:
        ServerSentEvent with OpenAI streaming format
    """
    # Validate messages using shared validation utility
    is_valid, error_message = validate_chat_messages(messages)
    if not is_valid:
        yield _to_openai_error(error_message)
        return

    # Extract query and history using shared utility
    query_text, conversation_history = extract_query_from_messages(messages)

    # Create a copy of settings with overrides (Settings is frozen, must use replace)
    overrides = {}
    if temperature is not None:
        overrides["temperature"] = temperature
    if max_tokens is not None:
        overrides["max_tokens"] = max_tokens
    if rag is not None:
        overrides["enable_rag"] = rag
    if thinking is not None:
        overrides["enable_thinking"] = thinking
    
    request_settings = replace(settings, **overrides) if overrides else settings

    chat_id = _generate_chat_id()
    logger.info(f"Starting OpenAI-compatible chat: {chat_id}")

    try:
        # Stream RAG query
        citations = []
        async for event in rag_stream_query(
            query_text, conversation_history, request_settings
        ):
            if event.get("type") == "token":
                # Convert token to OpenAI streaming format
                yield _to_openai_sse_event(
                    {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "model": "lifequery",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": event.get("content", "")},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
            elif event.get("type") == "citations":
                citations = event.get("citations", [])

        # Send final event with finish_reason
        yield _to_openai_sse_event(
            {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "model": "lifequery",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "x_citations": citations,
            }
        )

        # Send [DONE] marker
        yield ServerSentEvent(data="[DONE]")

        logger.info(f"OpenAI-compatible chat complete: {chat_id}")

    except Exception as e:
        logger.error(f"Error in OpenAI-compatible chat: {e}", exc_info=True)
        yield _to_openai_sse_event(
            {"error": {"message": str(e), "type": "server_error"}}
        )


async def chat_completion_non_streaming(
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    rag: bool | None = None,
    thinking: bool | None = None,
) -> OpenAIChatResponse:
    """Non-streaming OpenAI chat completion.

    Args:
        messages: List of message dicts with 'role' and 'content'
        temperature: Override temperature (optional)
        max_tokens: Override max_tokens (optional)

    Returns:
        OpenAIChatResponse with completion
    """
    # Validate messages using shared validation utility
    is_valid, error_message = validate_chat_messages(messages)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_message)

    # Extract query and history using shared utility
    query_text, conversation_history = extract_query_from_messages(messages)

    # Create a copy of settings with overrides (Settings is frozen, must use replace)
    overrides = {}
    if temperature is not None:
        overrides["temperature"] = temperature
    if max_tokens is not None:
        overrides["max_tokens"] = max_tokens
    if rag is not None:
        overrides["enable_rag"] = rag
    if thinking is not None:
        overrides["enable_thinking"] = thinking

    request_settings = replace(settings, **overrides) if overrides else settings

    chat_id = _generate_chat_id()
    logger.info(f"Starting OpenAI-compatible chat (non-streaming): {chat_id}")

    try:
        # Collect full response from RAG query
        full_content = []
        citations = []

        async for event in rag_stream_query(
            query_text, conversation_history, request_settings
        ):
            if event.get("type") == "token":
                full_content.append(event.get("content", ""))
            elif event.get("type") == "citations":
                citations = event.get("citations", [])

        # Combine content
        content = "".join(full_content)

        # Calculate usage (approximate)
        prompt_tokens = int(len(query_text.split()) * 1.35)
        completion_tokens = int(len(content.split()) * 1.35)
        total_tokens = prompt_tokens + completion_tokens

        response = OpenAIChatResponse(
            id=chat_id,
            model="lifequery",
            choices=[
                OpenAIChoice(
                    index=0,
                    message=OpenAIMessage(role="assistant", content=content),
                    finish_reason="stop",
                )
            ],
            usage=OpenAIUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ),
            x_citations=citations if citations else None,
        )

        logger.info(f"OpenAI-compatible chat complete (non-streaming): {chat_id}")
        return response

    except Exception as e:
        logger.error(f"Error in OpenAI-compatible chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models")
async def list_models(raw_request: Request):
    """List available models (OpenAI-compatible).

    Returns a single 'lifequery' model entry so OpenAI clients and
    tools like Open WebUI can discover and select it.
    """
    _verify_openai_api_key(raw_request)
    return {
        "object": "list",
        "data": [
            {
                "id": "lifequery",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "lifequery",
            },
            {
                "id": "lifequery-memory",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "lifequery",
            },
            {
                "id": "lifequery-chat",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "lifequery",
            },
        ],
    }


def _verify_openai_api_key(raw_request: Request) -> None:
    """Enforce optional API key auth for OpenAI-compatible endpoints."""
    if not settings.api_key:
        return

    auth_header = raw_request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning(
            "OpenAI request rejected: Missing or invalid Authorization header"
        )
        raise HTTPException(
            status_code=401, detail="Unauthorized: Missing or invalid API Key"
        )

    provided_key = auth_header.split(" ", 1)[1]
    if provided_key != settings.api_key:
        logger.warning("OpenAI request rejected: Invalid API Key")
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")


@router.post("/chat/completions")
async def create_chat_completion(request: OpenAIChatRequest, raw_request: Request):
    """Create a chat completion (OpenAI-compatible)."""
    return await _handle_openai_request(request, raw_request)


@router.post("/completions")
async def create_legacy_completion(raw_request: Request):
    """Legacy OpenAI completion shim (/v1/completions).
    
    Some older tools or internal OpenAI libraries attempt to hit this endpoint
    if they are not configured for 'Chat' mode. We shim this by taking either 
    the 'messages' or 'prompt' field and turning it into a chat request.
    """
    try:
        body = await raw_request.json()
        logger.debug(f"Legacy completion request body keys: {list(body.keys())}")
        
        # 1. Try to get messages (some clients send messages to /completions)
        messages_input = body.get("messages")
        
        # 2. Fallback to prompt (Standard legacy format)
        if not messages_input:
            prompt = body.get("prompt", "")
            if isinstance(prompt, list):
                prompt = " ".join([str(p) for p in prompt])
            elif not isinstance(prompt, str):
                prompt = str(prompt)
            
            messages_input = [{"role": "user", "content": prompt}]

        # Convert to OpenAIChatRequest
        chat_request = OpenAIChatRequest(
            model=body.get("model", "lifequery"),
            messages=messages_input,
            stream=body.get("stream", False),
            temperature=body.get("temperature", 0.3),
            max_tokens=body.get("max_tokens", 1024),
            rag=body.get("rag")
        )
        return await _handle_openai_request(chat_request, raw_request)
    except Exception as e:
        logger.error(f"Legacy completion shim error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Invalid legacy completion request: {str(e)}")


async def _handle_openai_request(request: OpenAIChatRequest, raw_request: Request):
    """Internal handler for OpenAI-compatible requests (Shared by chat and legacy shims)."""
    logger.info(
        f"Received OpenAI-compatible request: type={'stream' if request.stream else 'sync'}, "
        f"messages={len(request.messages)}"
    )

    _verify_openai_api_key(raw_request)

    # Convert messages to list of dicts for RAG pipeline
    messages_list = [msg.model_dump() for msg in request.messages]

    # Determine RAG override
    rag_override = request.rag
    if rag_override is None and request.model:
        model_name = request.model.lower()
        if "norag" in model_name or "regular" in model_name or "chat" in model_name:
            rag_override = False
        elif "rag" in model_name or "memory" in model_name:
            rag_override = True

    thinking_override = (
        request.enable_thinking
        if request.enable_thinking is not None
        else request.thinking
    )

    if request.stream:
        # Streaming response
        return EventSourceResponse(
            chat_completion_streaming_generator(
                messages_list,
                request.temperature,
                request.max_tokens,
                rag_override,
                thinking_override,
            ),
            headers={"X-Accel-Buffering": "no"},
        )
    else:
        # Non-streaming response
        return await chat_completion_non_streaming(
            messages_list,
            request.temperature,
            request.max_tokens,
            rag_override,
            thinking_override,
        )
