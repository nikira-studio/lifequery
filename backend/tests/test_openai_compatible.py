"""Unit tests for OpenAI-compatible endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Import main app to get all routers
from main import app

client = TestClient(app)


class TestOpenAICompatibleRouter:
    """Tests for /v1/chat/completions endpoint."""

    def test_chat_completion_streaming_basic(self):
        """Test streaming chat completion with basic request."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:

            async def mock_generator():
                yield {"type": "token", "content": "Hello"}
                yield {"type": "token", "content": " world"}
                yield {"type": "token", "content": "!"}
                yield {"type": "citations", "citations": []}

            mock_rag.return_value = mock_generator()

            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4",  # Should be ignored
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": True,
                },
            )
            # Should return SSE stream
            assert response.status_code == 200

    def test_chat_completion_non_streaming_basic(self):
        """Test non-streaming chat completion with basic request."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:

            async def mock_generator():
                yield {"type": "token", "content": "Hello"}
                yield {"type": "token", "content": " world"}
                yield {"type": "citations", "citations": []}

            mock_rag.return_value = mock_generator()

            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": False,
                },
            )
            # Should return JSON
            assert response.status_code == 200
            data = response.json()
            assert "id" in data
            assert data["object"] == "chat.completion"
            assert data["model"] == "lifequery"
            assert "choices" in data
            assert len(data["choices"]) == 1

    def test_chat_completion_with_temperature_override(self):
        """Test that temperature override works."""
        with patch("routers.openai_compatible.settings") as mock_settings:
            with patch(
                "routers.openai_compatible.rag_stream_query",
                new_callable=AsyncMock,
            ) as mock_rag:

                async def mock_generator():
                    yield {"type": "token", "content": "Hi"}
                    yield {"type": "citations", "citations": []}

                mock_rag.return_value = mock_generator()

                mock_settings.temperature = 0.7
                mock_settings.max_tokens = 1024

                # Non-streaming
                response = client.post(
                    "/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": "Hi"}],
                        "stream": False,
                        "temperature": 0.3,  # Override
                        "max_tokens": 512,  # Override
                    },
                )
                assert response.status_code == 200

    def test_chat_completion_with_citations(self):
        """Test that citations are included in response."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:
            citations = [
                {
                    "chat_name": "Work Group",
                    "date_range": "Nov 3–12, 2025",
                    "participants": ["You", "Alex"],
                }
            ]

            async def mock_generator():
                yield {"type": "token", "content": "Based"}
                yield {"type": "citations", "citations": citations}

            mock_rag.return_value = mock_generator()

            # Non-streaming
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "What?"}],
                    "stream": False,
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert "x_citations" in data
            assert data["x_citations"] == citations

    def test_chat_completion_no_messages_error(self):
        """Test that missing messages returns error."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:
            mock_rag.return_value = []

            response = client.post(
                "/v1/chat/completions",
                json={"messages": [], "stream": False},
            )
            # Should return error in OpenAI format
            assert response.status_code == 400

    def test_chat_completion_last_message_not_user_error(self):
        """Test that last message not from user returns error."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:
            mock_rag.return_value = []

            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [
                        {"role": "assistant", "content": "Hi"},
                        {"role": "user", "content": "Hello"},
                        {"role": "assistant", "content": "Bye"},  # Last should be user
                    ],
                    "stream": False,
                },
            )
            assert response.status_code == 400

    def test_chat_completion_empty_query_error(self):
        """Test that empty query returns error."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:
            mock_rag.return_value = []

            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": ""}],
                    "stream": False,
                },
            )
            assert response.status_code == 400

    def test_chat_completion_with_conversation_history(self):
        """Test that conversation history is included."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:

            async def mock_generator():
                yield {"type": "token", "content": "Response"}
                yield {"type": "citations", "citations": []}

            mock_rag.return_value = mock_generator()

            messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
                {"role": "user", "content": "How are you?"},
            ]

            response = client.post(
                "/v1/chat/completions", json={"messages": messages, "stream": False}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["choices"][0]["message"]["content"] == "Response"

    def test_chat_completion_usage_tokens_calculated(self):
        """Test that usage tokens are calculated."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:

            async def mock_generator():
                # "Hi there!" -> 3 words -> ~4 tokens
                yield {"type": "token", "content": "Hi"}
                yield {"type": "token", "content": " there!"}
                yield {"type": "citations", "citations": []}

            mock_rag.return_value = mock_generator()

            # Query: "Hello" -> 1 word -> ~1 token
            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": False,
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert "usage" in data
            # ~1 + ~4 = ~5 total tokens
            assert data["usage"]["total_tokens"] > 0

    def test_chat_completion_id_format(self):
        """Test that chat completion ID is in correct format."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:

            async def mock_generator():
                yield {"type": "token", "content": "Hi"}
                yield {"type": "citations", "citations": []}

            mock_rag.return_value = mock_generator()

            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": False,
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert "id" in data
            assert data["id"].startswith("chatcmpl-")

    def test_chat_completion_model_ignored(self):
        """Test that model field is ignored."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:

            async def mock_generator():
                yield {"type": "token", "content": "Hi"}
                yield {"type": "citations", "citations": []}

            mock_rag.return_value = mock_generator()

            # Provide various models, but response should always use "lifequery"
            for model_name in ["gpt-4", "gpt-3.5-turbo", "claude-3"]:
                response = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": model_name,
                        "messages": [{"role": "user", "content": "Hi"}],
                        "stream": False,
                    },
                )
                assert response.status_code == 200
                data = response.json()
                assert data["model"] == "lifequery"

    def test_chat_completion_default_streaming_true(self):
        """Test that stream defaults to true if not provided."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:

            async def mock_generator():
                yield {"type": "token", "content": "Hi"}
                yield {"type": "citations", "citations": []}

            mock_rag.return_value = mock_generator()

            # Don't specify stream parameter - should default to True
            response = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "Hi"}]},
            )
            # Should return SSE stream (status 200)
            assert response.status_code == 200

    def test_chat_completion_settings_restored_after(self):
        """Test that original settings are restored after request."""
        with patch("routers.openai_compatible.settings") as mock_settings:
            with patch(
                "routers.openai_compatible.rag_stream_query",
                new_callable=AsyncMock,
            ) as mock_rag:

                async def mock_generator():
                    yield {"type": "token", "content": "Hi"}
                    yield {"type": "citations", "citations": []}

                mock_rag.return_value = mock_generator()

            mock_settings.temperature = 0.7
            mock_settings.max_tokens = 1024

            # Request with overrides
            client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": False,
                    "temperature": 0.3,
                    "max_tokens": 512,
                },
            )

            # Settings should be restored after request
            assert mock_settings.temperature == 0.7
            assert mock_settings.max_tokens == 1024

    def test_chat_completion_finish_reason_stop(self):
        """Test that finish_reason is set to 'stop' for successful completion."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:

            async def mock_generator():
                yield {"type": "token", "content": "Hi"}
                yield {"type": "citations", "citations": []}

            mock_rag.return_value = mock_generator()

            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": False,
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["choices"][0]["finish_reason"] == "stop"

    def test_chat_completion_object_field(self):
        """Test that object field is correct."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:

            async def mock_generator():
                yield {"type": "token", "content": "Hi"}
                yield {"type": "citations", "citations": []}

            mock_rag.return_value = mock_generator()

            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": False,
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["object"] == "chat.completion"

    def test_chat_completion_message_role(self):
        """Test that message role is 'assistant'."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:

            async def mock_generator():
                yield {"type": "token", "content": "Hello"}
                yield {"type": "citations", "citations": []}

            mock_rag.return_value = mock_generator()

            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": False,
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["choices"][0]["message"]["role"] == "assistant"

    def test_chat_completion_choices_index(self):
        """Test that choices index is 0."""
        with patch(
            "routers.openai_compatible.rag_stream_query",
            new_callable=AsyncMock,
        ) as mock_rag:

            async def mock_generator():
                yield {"type": "token", "content": "Hi"}
                yield {"type": "citations", "citations": []}

            mock_rag.return_value = mock_generator()

            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": False,
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["choices"][0]["index"] == 0

    def test_chat_completion_requires_api_key_when_configured(self):
        """Chat completions should enforce Bearer auth when api_key is set."""
        with patch("routers.openai_compatible.settings") as mock_settings:
            mock_settings.api_key = "secret-key"

            response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": False,
                },
            )
            assert response.status_code == 401

            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer wrong"},
                json={
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": False,
                },
            )
            assert response.status_code == 401

    def test_models_requires_api_key_when_configured(self):
        """/v1/models should use the same auth policy as completions."""
        with patch("routers.openai_compatible.settings") as mock_settings:
            mock_settings.api_key = "secret-key"

            response = client.get("/v1/models")
            assert response.status_code == 401

            response = client.get(
                "/v1/models", headers={"Authorization": "Bearer secret-key"}
            )
            assert response.status_code == 200
            data = response.json()
            model_ids = [m["id"] for m in data["data"]]
            assert "lifequery" in model_ids

    def test_chat_completion_with_thinking_override(self):
        """Per-request thinking toggle should override global config."""
        with patch("routers.openai_compatible.replace") as mock_replace:
            with patch(
                "routers.openai_compatible.rag_stream_query",
                new_callable=AsyncMock,
            ) as mock_rag:

                async def mock_generator():
                    yield {"type": "token", "content": "Hi"}
                    yield {"type": "citations", "citations": []}

                mock_rag.return_value = mock_generator()
                mock_replace.return_value = MagicMock()

                response = client.post(
                    "/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": "Hi"}],
                        "stream": False,
                        "thinking": True,
                    },
                )

                assert response.status_code == 200
                mock_replace.assert_called()
                _, kwargs = mock_replace.call_args
                assert kwargs["enable_thinking"] is True

    def test_chat_completion_with_enable_thinking_alias(self):
        """Alias field should work for clients sending enable_thinking."""
        with patch("routers.openai_compatible.replace") as mock_replace:
            with patch(
                "routers.openai_compatible.rag_stream_query",
                new_callable=AsyncMock,
            ) as mock_rag:

                async def mock_generator():
                    yield {"type": "token", "content": "Hi"}
                    yield {"type": "citations", "citations": []}

                mock_rag.return_value = mock_generator()
                mock_replace.return_value = MagicMock()

                response = client.post(
                    "/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": "Hi"}],
                        "stream": False,
                        "enable_thinking": False,
                    },
                )

                assert response.status_code == 200
                _, kwargs = mock_replace.call_args
                assert kwargs["enable_thinking"] is False
