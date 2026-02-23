"""RAG assembly module - context building and prompt construction.

This module handles:
- Assembling context from retrieved chunks
- Respecting context token limits
- Building the final prompt for the LLM
"""

from typing import Optional

from chunker.chunker import estimate_tokens
from config import Settings, get_system_prompt
from utils.logger import get_logger
from vector_store.chroma import RetrievedChunk

logger = get_logger(__name__)


def build_context(
    chunks: list[RetrievedChunk],
    context_cap: int,
) -> tuple[str, list[RetrievedChunk], int]:
    """Build context text from retrieved chunks, respecting token limit.

    Args:
        chunks: List of retrieved chunks (already sorted by relevance)
        context_cap: Maximum token count for context

    Returns:
        Tuple of (context_text, used_chunks, token_count)
    """
    context_parts: list[str] = []
    token_count = 0
    used_chunks: list[RetrievedChunk] = []

    # Sort chunks by timestamp (newest first) to prioritize recent context
    chunks.sort(key=lambda x: x.timestamp_start, reverse=True)

    for chunk in chunks:
        # Build a chunk header
        from datetime import datetime

        start_dt = datetime.utcfromtimestamp(chunk.timestamp_start).strftime("%Y-%m-%d")
        end_dt = datetime.utcfromtimestamp(chunk.timestamp_end).strftime("%Y-%m-%d")

        header = f"--- CHAT: {chunk.chat_name or 'Unknown'} | DATES: {start_dt} to {end_dt} ---"
        chunk_text = f"{header}\n{chunk.content}"

        tokens = estimate_tokens(chunk_text)
        if token_count + tokens > context_cap:
            logger.debug(
                f"Context cap reached: {token_count} tokens, {len(context_parts)} chunks"
            )
            break
        context_parts.append(chunk_text)
        token_count += tokens
        used_chunks.append(chunk)

    if not context_parts:
        logger.warning("No chunks fit within context cap")
        return "", [], 0

    context_text = "\n\n".join(context_parts)
    logger.debug(
        f"Context assembled: {token_count} tokens, {len(context_parts)} chunks"
    )

    return context_text, used_chunks, token_count


def build_system_message(context_text: str, custom_prompt: str) -> str:
    """Build the system message with context.

    Args:
        context_text: The assembled context text
        custom_prompt: The dynamic system prompt from settings

    Returns:
        Formatted system message with context inserted
    """
    if "{context_text}" in custom_prompt:
        return custom_prompt.replace("{context_text}", context_text)
    return f"{custom_prompt}\n\n--- CONTEXT ---\n{context_text}"


def build_messages(
    query_text: str,
    system_message: str,
    conversation_history: list[dict],
) -> list[dict]:
    """Build the full message list for the LLM.

    Args:
        query_text: The user's question
        system_message: The system prompt with context
        conversation_history: Previous messages in the conversation

    Returns:
        List of message dicts ready for the LLM
    """
    # Qwen3 models can be sensitive to system prompt placement.
    # We'll put the instructions and context directly into the user message
    # as suggested by the user to improve instruction following.

    user_content = f"{system_message}\n\nQuestion: {query_text}"

    messages = [
        *conversation_history,
        {"role": "user", "content": user_content},
    ]
    return messages


def build_no_context_messages(
    query_text: str,
    conversation_history: list[dict],
    is_rag_disabled: bool = False,
    enable_thinking: bool = True,
) -> list[dict]:
    """Build messages for the case when no context is available.

    Args:
        query_text: The user's question
        conversation_history: Previous messages in the conversation
        is_rag_disabled: Whether RAG was intentionally disabled
        enable_thinking: Whether internal reasoning is enabled
    """
    if is_rag_disabled:
        system_content = "You are LifeQuery, a helpful and intelligent assistant. Answer the user's questions clearly and accurately."
    else:
        system_content = (
            "You are LifeQuery, a personal memory assistant. I couldn't find specific records in your Telegram history "
            "to answer this query with high precision, so I will answer based on my general knowledge. "
            "To help me find relevant details, ensure your chats are indexed and your query contains specific keywords."
        )

    if not enable_thinking:
        system_content = (
            f"INSTRUCTION: DO NOT provide internal reasoning or show your thought process. "
            f"Respond directly with the final answer.\n\n{system_content}"
        )
    else:
        # For models that don't natively use a separate reasoning field (like standard Qwen3, Llama 3),
        # we explicitly ask them to use <think> tags.
        system_content = (
            f"INSTRUCTION: If you need to reason or think step-by-step, wrap your internal monologue "
            f"entirely within <think> and </think> tags before providing your final answer.\n\n{system_content}"
        )

    # Consistent with build_messages: put system content into user message
    user_content = f"{system_content}\n\nQuestion: {query_text}"

    messages = [
        *conversation_history,
        {"role": "user", "content": user_content},
    ]
    return messages


async def assemble(
    query_text: str,
    chunks: list[RetrievedChunk],
    settings: Settings,
    conversation_history: Optional[list[dict]] = None,
) -> tuple[list[dict], list[RetrievedChunk]]:
    """Main assembly entry point - build context and messages for LLM.

    This is a convenience function that combines context building
    and message construction in a single call.

    Args:
        query_text: The user's question
        chunks: Retrieved chunks from vector store
        settings: Settings containing context_cap and other config
        conversation_history: Optional previous messages

    Returns:
        Tuple of (messages_for_llm, used_chunks)
    """
    if conversation_history is None:
        conversation_history = []

    # Build context from chunks
    context_text, used_chunks, token_count = build_context(chunks, settings.context_cap)

    if not context_text:
        # No context available - use fallback messages
        messages = build_no_context_messages(
            query_text, conversation_history, is_rag_disabled=not settings.enable_rag
        )
        return messages, []

    # Build system message with context
    system_message = build_system_message(context_text, get_system_prompt())

    # If thinking is disabled, add a directive to avoid internal reasoning
    if not settings.enable_thinking:
        system_message = (
            f"INSTRUCTION: DO NOT provide internal reasoning or show your thought process. "
            f"Respond directly with the final answer based on the context.\n\n{system_message}"
        )
    else:
        system_message = (
            f"INSTRUCTION: If you need to reason or think step-by-step, wrap your internal monologue "
            f"entirely within <think> and </think> tags before providing your final answer.\n\n{system_message}"
        )

    # Build final message list
    messages = build_messages(query_text, system_message, conversation_history)

    logger.info(f"Assembled {len(used_chunks)} chunks ({token_count} tokens) for query")

    return messages, used_chunks
