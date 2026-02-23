"""Unit tests for FastAPI routers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Import the main app to get all routers
from main import app

client = TestClient(app)


class TestHealthEndpoint:
    """Tests for GET /api/health endpoint."""

    def test_health_check(self):
        """Test health check returns correct status."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.0"


class TestSettingsRouter:
    """Tests for /api/settings endpoints."""

    def test_get_settings(self):
        """Test GET /api/settings returns all settings."""
        response = client.get("/api/settings")
        assert response.status_code == 200
        data = response.json()

        # Check that all expected fields are present
        expected_fields = [
            "telegram_api_id",
            "telegram_api_hash",
            "ollama_url",
            "embedding_model",
            "chat_provider",
            "chat_model",
            "chat_url",
            "openrouter_api_key",
            "custom_chat_url",
            "temperature",
            "max_tokens",
            "top_k",
            "context_cap",
            "chunk_target",
            "chunk_max",
            "chunk_overlap",
        ]
        for field in expected_fields:
            assert field in data

    def test_update_settings_valid(self):
        """Test POST /api/settings with valid updates."""
        with patch("routers.settings.save_to_db", new_callable=AsyncMock) as mock_save:
            mock_save.return_value = MagicMock()

            response = client.post(
                "/api/settings",
                json={"ollama_url": "http://new-url:11434", "temperature": 0.5},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True

    def test_update_settings_empty_values_ignored(self):
        """Test POST /api/settings ignores empty string values."""
        with patch("routers.settings.save_to_db", new_callable=AsyncMock) as mock_save:
            mock_save.return_value = MagicMock()

            response = client.post(
                "/api/settings", json={"ollama_url": "", "temperature": 0.5}
            )
            assert response.status_code == 200
            # Should only update temperature, not ollama_url
            mock_save.assert_called_once()
            call_args = mock_save.call_args[0][0]
            assert "ollama_url" not in call_args
            assert call_args["temperature"] == 0.5

    def test_update_settings_none_values_ignored(self):
        """Test POST /api/settings ignores None values."""
        with patch("routers.settings.save_to_db", new_callable=AsyncMock) as mock_save:
            mock_save.return_value = MagicMock()

            response = client.post(
                "/api/settings", json={"ollama_url": None, "temperature": 0.5}
            )
            assert response.status_code == 200
            mock_save.assert_called_once()
            call_args = mock_save.call_args[0][0]
            assert "ollama_url" not in call_args

    def test_update_settings_empty_body(self):
        """Test POST /api/settings with empty body."""
        with patch("routers.settings.save_to_db", new_callable=AsyncMock) as mock_save:
            mock_save.return_value = MagicMock()

            response = client.post("/api/settings", json={})
            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True
            # Should not call save_to_db for empty body
            mock_save.assert_not_called()

    def test_update_settings_error_handling(self):
        """Test POST /api/settings handles errors gracefully."""
        with patch("routers.settings.save_to_db", new_callable=AsyncMock) as mock_save:
            mock_save.side_effect = Exception("Database error")

            response = client.post("/api/settings", json={"temperature": 0.5})
            assert response.status_code == 500


class TestTelegramAuthRouter:
    """Tests for /api/telegram endpoints."""

    def test_get_status_uninitialized(self):
        """Test GET /api/telegram/status returns uninitialized."""
        with patch("routers.telegram_auth.settings") as mock_settings:
            mock_settings.telegram_api_id = ""
            mock_settings.telegram_api_hash = ""

            response = client.get("/api/telegram/status")
            assert response.status_code == 200
            data = response.json()
            assert data["state"] == "uninitialized"

    def test_get_status_needs_auth(self):
        """Test GET /api/telegram/status returns needs_auth when no session stored."""
        import asyncio

        async def no_session():
            return None

        with patch("routers.telegram_auth.settings") as mock_settings:
            mock_settings.telegram_api_id = "12345678"
            mock_settings.telegram_api_hash = "abc123"

            with patch("telegram.telethon_sync._load_session_string", no_session):
                response = client.get("/api/telegram/status")
                assert response.status_code == 200
                data = response.json()
                assert data["state"] == "needs_auth"

    def test_auth_start_no_credentials(self):
        """Test POST /api/telegram/auth/start with no credentials."""
        with patch("routers.telegram_auth.settings") as mock_settings:
            mock_settings.telegram_api_id = ""
            mock_settings.telegram_api_hash = ""

            response = client.post(
                "/api/telegram/auth/start", json={"phone": "+12125551234"}
            )
            assert response.status_code == 400

    def test_auth_start_success(self):
        """Test POST /api/telegram/auth/start succeeds."""
        with patch("routers.telegram_auth.settings") as mock_settings:
            mock_settings.telegram_api_id = "12345678"
            mock_settings.telegram_api_hash = "abc123"

            with patch(
                "routers.telegram_auth.start_auth", new_callable=AsyncMock
            ) as mock_auth:
                mock_auth.return_value = {"state": "phone_sent"}

                response = client.post(
                    "/api/telegram/auth/start", json={"phone": "+12125551234"}
                )
                assert response.status_code == 200
                data = response.json()
                assert data["state"] == "phone_sent"

    def test_auth_start_with_2fa(self):
        """Test POST /api/telegram/auth/start with 2FA."""
        with patch("routers.telegram_auth.settings") as mock_settings:
            mock_settings.telegram_api_id = "12345678"
            mock_settings.telegram_api_hash = "abc123"

            with patch(
                "routers.telegram_auth.start_auth", new_callable=AsyncMock
            ) as mock_auth:
                mock_auth.return_value = {"state": "phone_sent", "token": "abc123token"}

                response = client.post(
                    "/api/telegram/auth/start", json={"phone": "+12125551234"}
                )
                assert response.status_code == 200
                data = response.json()
                assert data["state"] == "phone_sent"
                assert "token" in data

    def test_auth_verify_success_with_token(self):
        """Test POST /api/telegram/auth/verify with token succeeds."""
        with patch(
            "routers.telegram_auth.verify_auth", new_callable=AsyncMock
        ) as mock_verify:
            mock_verify.return_value = {"state": "connected"}

            response = client.post(
                "/api/telegram/auth/verify",
                json={"token": "test-token", "code": "12345"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["state"] == "connected"

    def test_auth_verify_wrong_code(self):
        """Test POST /api/telegram/auth/verify with wrong code."""
        with patch(
            "routers.telegram_auth.verify_auth", new_callable=AsyncMock
        ) as mock_verify:
            mock_verify.return_value = {"state": "phone_sent", "error": "Invalid code"}

            response = client.post(
                "/api/telegram/auth/verify",
                json={"token": "test-token", "code": "00000"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["state"] == "phone_sent"
            assert "error" in data

    def test_auth_verify_with_phone(self):
        """Test POST /api/telegram/auth/verify with phone and code."""
        with patch(
            "routers.telegram_auth.verify_auth", new_callable=AsyncMock
        ) as mock_verify:
            mock_verify.return_value = {"state": "connected"}

            with patch("routers.telegram_auth._auth_tokens") as mock_tokens:
                mock_tokens.__getitem__ = MagicMock(return_value="test-token")

                response = client.post(
                    "/api/telegram/auth/verify",
                    json={"phone": "+12125551234", "code": "12345"},
                )
                assert response.status_code == 200

    def test_auth_verify_2fa_required(self):
        """Test POST /api/telegram/auth/verify with 2FA required."""
        with patch(
            "routers.telegram_auth.verify_auth", new_callable=AsyncMock
        ) as mock_verify:
            mock_verify.side_effect = ValueError("Two-step verification required")

            response = client.post(
                "/api/telegram/auth/verify",
                json={"phone": "+12125551234", "code": "12345"},
            )
            assert response.status_code == 400

    def test_disconnect_success(self):
        """Test POST /api/telegram/disconnect succeeds."""
        with patch(
            "routers.telegram_auth.disconnect_telegram", new_callable=AsyncMock
        ) as mock_disconnect:
            mock_disconnect.return_value = {"state": "needs_auth"}

            response = client.post("/api/telegram/disconnect")
            assert response.status_code == 200
            data = response.json()
            assert data["state"] == "needs_auth"


class TestDataRouter:
    """Tests for /api/stats endpoint."""

    def test_get_stats(self):
        """Test GET /api/stats returns correct statistics."""
        with patch(
            "routers.data.get_connection", new_callable=AsyncMock
        ) as mock_get_conn:
            # Setup mock connection
            mock_conn = AsyncMock()
            mock_get_conn.return_value = mock_conn

            # Mock query responses
            async def mock_execute(query, *args):
                cursor = MagicMock()
                if "COUNT(*) FROM messages" in query:
                    cursor.fetchone.return_value = (142847,)
                elif "COUNT(*) FROM chunks" in query:
                    cursor.fetchone.return_value = (12403,)
                elif "COUNT(*) FROM chunks WHERE embedded_at" in query:
                    cursor.fetchone.return_value = (12280,)
                elif "COUNT(DISTINCT chat_id)" in query:
                    cursor.fetchone.return_value = (238,)
                elif "sync_log" in query:
                    cursor.fetchone.return_value = (1697422320, 847, 203)
                return cursor

            mock_conn.execute = mock_execute

            response = client.get("/api/stats")
            assert response.status_code == 200
            data = response.json()
            assert data["message_count"] == 142847
            assert data["chunk_count"] == 12403
            assert data["chat_count"] == 238
            assert data["embedded_count"] == 12280
            assert data["last_sync_added"] == 847

    def test_get_stats_no_sync_yet(self):
        """Test GET /api/stats when no sync has been done yet."""
        with patch(
            "routers.data.get_connection", new_callable=AsyncMock
        ) as mock_get_conn:
            mock_conn = AsyncMock()
            mock_get_conn.return_value = mock_conn

            async def mock_execute(query, *args):
                cursor = MagicMock()
                if "sync_log" in query:
                    cursor.fetchone.return_value = None
                else:
                    cursor.fetchone.return_value = (0,)
                return cursor

            mock_conn.execute = mock_execute

            response = client.get("/api/stats")
            assert response.status_code == 200
            data = response.json()
            assert data["message_count"] == 0
            assert data["last_sync"] is None
            assert data["last_sync_added"] == 0

    def test_reindex_requires_confirmation(self):
        """Test POST /api/reindex requires confirm=true."""
        response = client.post("/api/reindex", json={"confirm": False})
        assert response.status_code == 400

    def test_reindex_with_confirmation(self):
        """Test POST /api/reindex with confirm=true."""
        # This would normally start an SSE stream, but we're just checking
        # that the endpoint accepts the request
        response = client.post("/api/reindex", json={"confirm": True})
        # Should return EventSourceResponse, but TestClient doesn't handle SSE
        # We'll just check it doesn't error before streaming
        assert response.status_code == 200


class TestChatRouter:
    """Tests for /api/chat endpoint."""

    @pytest.mark.asyncio
    async def test_chat_no_messages(self):
        """Test POST /api/chat with no messages."""
        with patch("routers.chat.rag_stream_query", new_callable=AsyncMock) as mock_rag:
            mock_rag.return_value = []

            response = client.post("/api/chat", json={"messages": []})
            # Should return SSE stream
            assert response.status_code == 200

    def test_chat_with_valid_messages(self):
        """Test POST /api/chat with valid messages."""
        with patch("routers.chat.rag_stream_query", new_callable=AsyncMock) as mock_rag:

            async def mock_generator():
                yield {"type": "token", "content": "Hello"}
                yield {"type": "token", "content": " world"}
                yield {"type": "citations", "citations": []}

            mock_rag.return_value = mock_generator()

            response = client.post(
                "/api/chat", json={"messages": [{"role": "user", "content": "Hi"}]}
            )
            # Should return SSE stream
            assert response.status_code == 200

    def test_chat_non_user_last_message(self):
        """Test POST /api/chat with last message not from user."""
        with patch("routers.chat.rag_stream_query", new_callable=AsyncMock) as mock_rag:

            async def mock_generator():
                yield {"type": "error", "message": "Last message must be from user"}

            mock_rag.return_value = mock_generator()

            response = client.post(
                "/api/chat", json={"messages": [{"role": "assistant", "content": "Hi"}]}
            )
            assert response.status_code == 200

    def test_chat_empty_query(self):
        """Test POST /api/chat with empty query."""
        with patch("routers.chat.rag_stream_query", new_callable=AsyncMock) as mock_rag:

            async def mock_generator():
                yield {"type": "error", "message": "Query cannot be empty"}

            mock_rag.return_value = mock_generator()

            response = client.post(
                "/api/chat", json={"messages": [{"role": "user", "content": ""}]}
            )
            assert response.status_code == 200

    def test_chat_with_conversation_history(self):
        """Test POST /api/chat with conversation history."""
        with patch("routers.chat.rag_stream_query", new_callable=AsyncMock) as mock_rag:

            async def mock_generator():
                yield {"type": "token", "content": "Response"}
                yield {"type": "citations", "citations": []}

            mock_rag.return_value = mock_generator()

            messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
                {"role": "user", "content": "How are you?"},
            ]
            response = client.post("/api/chat", json={"messages": messages})
            assert response.status_code == 200


class TestCORS:
    """Tests for CORS configuration."""

    def test_cors_headers_present(self):
        """Test that CORS headers are present on OPTIONS request."""
        response = client.options("/api/health")
        assert response.status_code == 200
        # Check for common CORS headers
        assert "access-control-allow-origin" in response.headers
