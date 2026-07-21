"""RAG query pipeline for LifeQuery - orchestration module.

This module provides the main entry point for RAG queries by orchestrating
the retrieval, assembly, and formatting modules.
"""

from typing import AsyncGenerator

from config import Settings, settings
from embedding.ollama_embedder import check_model_exists
from llm.client import get_llm_client
from utils.logger import get_logger

from .assemble import assemble
from .format import (
    format_citations,
    format_citations_event,
    format_debug,
    format_error,
    format_token,
)
from .retrieve import retrieve

logger = get_logger(__name__)


async def rag_stream_query(
    query_text: str,
    conversation_history: list[dict] | None = None,
    runtime_settings: Settings | None = None,
) -> AsyncGenerator[dict, None]:
    """Perform RAG query and stream the LLM response with citations.

    This is the main entry point for RAG queries. It orchestrates:
    1. Retrieval - embed query and fetch relevant chunks from vector store
    2. Assembly - build context from chunks and construct prompt
    3. Inference - stream LLM response
    4. Formatting - yield citations for the user

    Args:
        query_text: The user's question
        conversation_history: Optional list of previous message dicts with 'role' and 'content'
        runtime_settings: Optional Settings dataclass to use instead of global settings.
                         This allows per-request overrides without mutating global state.

    Yields:
        Dict with 'type' key:
        - {'type': 'token', 'content': str} - tokens from the LLM
        - {'type': 'citations', 'citations': list[dict]} - citation information at the end
        - {'type': 'error', 'message': str} - if an error occurs
    """
    # Use runtime_settings if provided, otherwise fall back to global settings
    active_settings = runtime_settings if runtime_settings is not None else settings

    try:
        if conversation_history is None:
            conversation_history = []

        logger.info(f"Starting RAG query: {query_text[:100]}...")

        # Pre-flight check: is the model available?
        if active_settings.chat_provider == "ollama":
            if not await check_model_exists(active_settings.chat_model):
                logger.warning(
                    f"Chat model '{active_settings.chat_model}' not detected in Ollama list. "
                    "Attempting to proceed as it may be pulled on demand."
                )

        # Step 1: Retrieval - embed query and get relevant chunks
        chunks = []
        if active_settings.enable_rag:
            try:
                chunks, included_chat_ids = await retrieve(query_text, active_settings)
            except Exception as e:
                logger.error(f"Step 1: Retrieval failed with error: {e}", exc_info=True)
                # Fallback: if retrieval fails (e.g. embedding model missing),
                # continue without context rather than erroring out
                logger.warning(
                    "Falling back to chat without context due to retrieval failure."
                )
                chunks = []
        else:
            logger.info("RAG is disabled in settings - skipping retrieval")

        if not chunks:
            if active_settings.enable_rag:
                logger.warning("No relevant chunks found for query")

            # Still stream a response from LLM indicating no context
            llm_client = get_llm_client(active_settings)
            from .assemble import build_no_context_messages

            messages = build_no_context_messages(
                query_text,
                conversation_history,
                is_rag_disabled=not active_settings.enable_rag,
                enable_thinking=active_settings.enable_thinking,
            )
            # Emit debug info even for no-context fallback
            from config import get_current_date, get_user_name
            user_name = get_user_name()
            current_date = get_current_date()
            yield format_debug(messages, user_name, current_date)

            async for token in llm_client.stream_chat(messages):
                yield format_token(token)
            yield format_citations_event([])
            return

        # Step 2: Assembly - build context and construct messages
        logger.debug("Step 2: Assembling context and building prompt")
        messages, used_chunks = await assemble(
            query_text,
            chunks,
            active_settings,
            conversation_history,
        )

        if not used_chunks:
            logger.warning("No chunks fit within context cap")
            yield format_error("No context could be assembled within token limit")
            return

        # Emit debug info with full messages sent to LLM
        from config import get_current_date, get_user_name

        user_name = get_user_name()
        current_date = get_current_date()
        yield format_debug(messages, user_name, current_date)

        # Step 3: Stream inference
        logger.debug("Step 3: Streaming inference from LLM")

        llm_client = get_llm_client(active_settings)
        async for token in llm_client.stream_chat(messages):
            yield format_token(token)

        # Step 4: Yield citations
        logger.debug("Step 4: Formatting and yielding citations")
        citations = format_citations(used_chunks)
        yield format_citations_event(citations)

        logger.info(f"RAG query complete: {len(citations)} citations provided")

    except Exception as e:
        logger.error(f"Error in RAG query: {e}", exc_info=True)
        from utils.error_beautifier import beautify_error

        yield format_error(beautify_error(e))
