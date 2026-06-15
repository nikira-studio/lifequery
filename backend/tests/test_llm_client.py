"""Tests for provider-specific LLM client request shaping."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from llm.client import UnifiedLLMClient


class EmptyAsyncStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def _client_for(base_url: str, *, enable_thinking: bool) -> UnifiedLLMClient:
    client = UnifiedLLMClient(
        base_url=base_url,
        api_key="test-key",
        model="test-model",
        enable_thinking=enable_thinking,
    )
    create = AsyncMock(return_value=EmptyAsyncStream())
    client.client = SimpleNamespace(
        base_url=base_url,
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    return client


async def _drain(client: UnifiedLLMClient) -> None:
    async for _token in client.stream_chat([{"role": "user", "content": "hi"}]):
        pass


@pytest.mark.asyncio
async def test_minimax_thinking_enabled_omits_boolean_thinking_field():
    client = _client_for("https://api.minimax.io/v1", enable_thinking=True)

    await _drain(client)

    kwargs = client.client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"] == {
        "think": True,
        "include_reasoning": True,
        "reasoning_split": True,
    }
    assert "thinking" not in kwargs["extra_body"]


@pytest.mark.asyncio
async def test_minimax_thinking_disabled_splits_reasoning_without_think_flags():
    client = _client_for("https://api.minimax.io/v1", enable_thinking=False)

    await _drain(client)

    kwargs = client.client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"] == {
        "include_reasoning": False,
        "reasoning_split": True,
    }


@pytest.mark.asyncio
async def test_zai_thinking_enabled_omits_boolean_thinking_field():
    client = _client_for("https://api.z.ai/api/coding/paas/v4", enable_thinking=True)

    await _drain(client)

    kwargs = client.client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"] == {"think": True}
