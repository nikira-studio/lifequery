"""Unified LLM client for LifeQuery - supports Ollama, OpenRouter, and Custom OpenAI-compatible endpoints."""

from typing import Any, AsyncGenerator

from config import Settings
from openai import AsyncOpenAI
from utils.logger import get_logger
import httpx
import json

logger = get_logger(__name__)


class OllamaNativeClient:
    """LLM client using Ollama's native API.

    Ollama's /v1/chat/completions (OpenAI-compat) ignores the 'think' parameter
    for Qwen3 and similar thinking models, causing all output to go into the
    'reasoning' field with empty 'content'. The native /api/chat endpoint
    properly respects think=False and returns content directly.
    """

    def __init__(
        self,
        host: str,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 1024,
        enable_thinking: bool = False,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.enable_thinking = enable_thinking
        self.host = host.rstrip("/")
        logger.info(f"OllamaNativeClient: host={self.host}, model={model}, think={enable_thinking}")

    async def stream_chat(
        self, messages: list[dict[str, Any]]
    ) -> AsyncGenerator[str, None]:
        """Stream chat using Ollama's native async client."""
        try:
            url = f"{self.host}/api/chat"
            options = {"temperature": self.temperature, "num_predict": self.max_tokens}
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": True,
                "options": options
            }
            if self.enable_thinking:
                # Hint Ollama to separate reasoning into its own field if the model supports it
                payload["include_reasoning"] = True

            reasoning_started = False
            async with httpx.AsyncClient(timeout=120.0) as http_client:
                async with http_client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                            
                        chunk = json.loads(line)
                        msg = chunk.get("message", {})
                        
                        # Handle reasoning field (Ollama/DeepSeek/Qwen variants)
                        reasoning = (
                            msg.get("reasoning", "") or 
                            msg.get("thinking", "") or 
                            msg.get("thought", "") or
                            msg.get("thought_content", "")
                        )
                        if reasoning:
                            if self.enable_thinking:
                                if not reasoning_started:
                                    yield "<think>"
                                    reasoning_started = True
                                yield reasoning
                            # If not enabled, we discard tokens in the reasoning field
                            continue
                        
                        # Handle main content
                        content = msg.get("content", "")
                        if content:
                            # If we were in reasoning, close the tag before yielding content
                            if reasoning_started:
                                yield "</think>"
                                reasoning_started = False
                            
                            # Fallback: if model leaks <think> tags into content (common in Qwen3/DeepSeek)
                            # and thinking is disabled, we strip them.
                            if not self.enable_thinking:
                                if "<think>" in content:
                                    # Very basic stripping for atomic tag tokens
                                    content = content.replace("<think>", "")
                                    # If it also contains the close tag, strip that too
                                    if "</think>" in content:
                                        content = content.replace("</think>", "")
                                    
                                    # If it's just raw text between tags, it might still leak,
                                    # but typically Ollama models use reasoning field.
                            
                            if content:
                                yield content

            # Final safety close if stream finishes in reasoning
            if reasoning_started:
                yield "</think>"

        except Exception as e:
            logger.error(f"OllamaNativeClient.stream_chat error: {e}", exc_info=True)
            raise


class UnifiedLLMClient:
    """Unified LLM client that handles all providers using OpenAI library."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 1024,
        enable_thinking: bool = False,
    ):
        """Initialize the unified LLM client.

        Args:
            base_url: Base URL for the API (must include /v1)
            api_key: API key (empty string for Ollama, actual key for others)
            model: Model name to use
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            enable_thinking: Whether to enable extended reasoning (Ollama only)
        """
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.enable_thinking = enable_thinking

        # Ensure URL ends with /v1 only if no version is detected
        if base_url:
            base_url = base_url.rstrip("/")
            # Check if URL already has a version-like suffix (e.g., /v1, /v4, /v1beta)
            import re
            if not re.search(r'/v\d+([a-z0-9_-]*)?$', base_url):
                base_url = base_url + "/v1"

        # For Ollama, use empty key if none provided
        if not api_key:
            api_key = "not-needed"

        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=60.0,
            max_retries=2,
        )

        logger.info(
            f"UnifiedLLMClient: base_url={base_url}, model={model}, "
            f"temperature={temperature}, max_tokens={max_tokens}"
        )

    async def stream_chat(
        self, messages: list[dict[str, Any]]
    ) -> AsyncGenerator[str, None]:
        """Stream chat completion from the LLM.

        Args:
            messages: List of message dicts with 'role' and 'content' keys

        Yields:
            Tokens (str) from the LLM response
        """
        logger.debug(
            f"UnifiedLLMClient.stream_chat: Starting with {len(messages)} messages, "
            f"model={self.model}"
        )

        try:
            # Only pass 'think' if explicitly enabled, or if we want to be explicit.
            # Many providers (OpenRouter, GLM) might not support this in extra_body.
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "stream": True,
            }
            if self.enable_thinking:
                # Most providers use 'think' or 'thinking'.
                # For GLM (api.z.ai), we avoid 'thinking' as it crashes on boolean.
                if "api.z.ai" in str(self.client.base_url):
                    kwargs["extra_body"] = {"think": True}
                else:
                    kwargs["extra_body"] = {"think": True, "thinking": True, "include_reasoning": True}
            elif "openai.com" not in str(self.client.base_url):
                # Don't send 'thinking' or 'think' in False state to avoid 400s
                # Use standard 'include_reasoning' for suppression.
                kwargs["extra_body"] = {"include_reasoning": False}
                
                # Minimax naturally packs <think> into the main content stream.
                # Setting reasoning_split=True forces them into a separate field we can safely ignore.
                if "api.minimax.io" in str(self.client.base_url):
                    kwargs["extra_body"]["reasoning_split"] = True

            response = await self.client.chat.completions.create(**kwargs)

            reasoning_started = False
            async for chunk in response:
                if not chunk.choices:
                    continue
                    
                delta = chunk.choices[0].delta
                
                # Extract reasoning token (DeepSeek uses 'reasoning_content', OpenRouter uses 'reasoning')
                reasoning_token = None
                if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                    reasoning_token = delta.reasoning_content
                elif hasattr(delta, "reasoning") and delta.reasoning:
                    reasoning_token = delta.reasoning
                elif hasattr(delta, "thought") and delta.thought:
                    reasoning_token = delta.thought
                elif hasattr(delta, "thought_content") and delta.thought_content:
                    reasoning_token = delta.thought_content
                
                # Check model_extra just in case the OpenAI client didn't map the attribute
                if not reasoning_token and hasattr(delta, "model_extra") and delta.model_extra:
                    reasoning_token = (
                        delta.model_extra.get("reasoning_content") or 
                        delta.model_extra.get("reasoning") or
                        delta.model_extra.get("thought") or
                        delta.model_extra.get("thought_content")
                    )
                
                if reasoning_token:
                    if self.enable_thinking:
                        if not reasoning_started:
                            yield "<think>"
                            reasoning_started = True
                        yield reasoning_token
                    # If not enabled, we simply discard these tokens
                else:
                    # If we have main content or end of stream, and we were reasoning, close it
                    if reasoning_started and (delta.content or chunk.choices[0].finish_reason):
                        yield "</think>"
                        reasoning_started = False
                
                # Yield main content
                if delta.content:
                    content = delta.content
                    # Fallback: strip <think> tags if leaked into content while thinking is disabled
                    if not self.enable_thinking:
                        if "<think>" in content:
                            content = content.replace("<think>", "")
                        if "</think>" in content:
                            content = content.replace("</think>", "")
                    
                    if content:
                        yield content

            # Safety close
            if reasoning_started:
                yield "</think>"

        except Exception as e:
            logger.error(f"UnifiedLLMClient.stream_chat error: {e}", exc_info=True)
            raise


def get_llm_client(settings: Settings) -> UnifiedLLMClient:
    """Factory function to get the unified LLM client based on settings.

    Args:
        settings: Settings object containing chat provider configuration

    Returns:
        UnifiedLLMClient instance

    Raises:
        ValueError: If chat_provider is not recognized
    """
    provider = settings.chat_provider
    
    # Common settings
    model = settings.chat_model
    temp = settings.temperature
    max_tokens = settings.max_tokens
    thinking = settings.enable_thinking

    if provider == "ollama":
        # Ollama: use native client so think=False is respected.
        # The OpenAI-compatible /v1 endpoint ignores think=False for Qwen3 models,
        # causing all output to go into 'reasoning' with empty 'content'.
        logger.info(f"Creating OllamaNativeClient: host={settings.chat_url}, model={model}")
        return OllamaNativeClient(
            host=settings.chat_url,
            model=model,
            temperature=temp,
            max_tokens=max_tokens,
            enable_thinking=thinking,
        )

    elif provider == "openrouter":
        # OpenRouter: use chat_url if customized, else use default OpenRouter URL
        url = settings.chat_url
        if not url or "ollama" in url:
            url = "https://openrouter.ai/api/v1"
            
        api_key = settings.chat_api_key or settings.openrouter_api_key
        
        logger.info(f"Creating UnifiedLLMClient for OpenRouter: url={url}, model={model}")
        return UnifiedLLMClient(
            base_url=url,
            api_key=api_key,
            model=model,
            temperature=temp,
            max_tokens=max_tokens,
            enable_thinking=thinking,
        )

    elif provider == "openai":
        # OpenAI: https://api.openai.com/v1
        url = settings.chat_url
        if not url or any(x in url for x in ["ollama", "openrouter", "minimax", "api.z.ai"]):
            url = "https://api.openai.com/v1"
            
        api_key = settings.chat_api_key or settings.openrouter_api_key
        # Default model if none picked
        active_model = model if model and model != "qwen3:8b" else "gpt-4o-mini"

        logger.info(f"Creating UnifiedLLMClient for OpenAI: url={url}, model={active_model}")
        return UnifiedLLMClient(
            base_url=url,
            api_key=api_key,
            model=active_model,
            temperature=temp,
            max_tokens=max_tokens,
            enable_thinking=thinking,
        )

    elif provider == "minimax":
        # MiniMax: https://api.minimax.io/v1
        url = settings.chat_url
        if not url or "ollama" in url or "openrouter" in url:
            url = "https://api.minimax.io/v1"
        
        api_key = settings.chat_api_key or settings.openrouter_api_key
        # Default model if none picked
        active_model = model if model and model != "qwen3:8b" else "MiniMax-M2.5"

        logger.info(f"Creating UnifiedLLMClient for MiniMax: url={url}, model={active_model}")
        return UnifiedLLMClient(
            base_url=url,
            api_key=api_key,
            model=active_model,
            temperature=temp,
            max_tokens=max_tokens,
            enable_thinking=thinking,
        )

    elif provider == "glmai":
        # Z.AI (GLM): https://api.z.ai/api/coding/paas/v4
        url = settings.chat_url
        if not url or "ollama" in url or "openrouter" in url:
            url = "https://api.z.ai/api/coding/paas/v4"
            
        api_key = settings.chat_api_key or settings.openrouter_api_key
        # Default model if none picked
        active_model = model if model and model != "qwen3:8b" else "glm-4.7"

        logger.info(f"Creating UnifiedLLMClient for GLM: url={url}, model={active_model}")
        return UnifiedLLMClient(
            base_url=url,
            api_key=api_key,
            model=active_model,
            temperature=temp,
            max_tokens=max_tokens,
            enable_thinking=thinking,
        )

    elif provider == "custom":
        # Custom: use chat_url and chat_api_key
        # Fallback to deprecated custom_chat_url if chat_url is still default and custom_chat_url is set
        url = settings.chat_url
        if ("ollama" in url or not url) and settings.custom_chat_url:
            url = settings.custom_chat_url
            
        api_key = settings.chat_api_key or settings.openrouter_api_key
        
        logger.info(f"Creating UnifiedLLMClient for Custom: url={url}, model={model}")
        return UnifiedLLMClient(
            base_url=url,
            api_key=api_key,
            model=model,
            temperature=temp,
            max_tokens=max_tokens,
            enable_thinking=thinking,
        )

    else:
        raise ValueError(f"Unknown chat provider: {provider}")
