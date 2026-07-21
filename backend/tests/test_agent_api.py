"""Tests for the agent-facing API surface."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def _range_payload(**overrides):
    payload = {
        "start": "2026-06-01T00:00:00Z",
        "end": "2026-06-08T00:00:00Z",
    }
    payload.update(overrides)
    return payload


class TestAgentApi:
    def test_query_messages_requires_api_key_when_configured(self):
        with patch("utils.auth.settings") as mock_settings:
            mock_settings.api_key = "secret-key"
            response = client.post(
                "/api/agent/messages/query",
                json=_range_payload(),
            )
            assert response.status_code == 401

            response = client.post(
                "/api/agent/messages/query",
                headers={"Authorization": "Bearer wrong"},
                json=_range_payload(),
            )
            assert response.status_code == 401

    def test_query_messages_filters_and_paginates(self):
        rows = [
            {
                "id": 10,
                "message_id": "m10",
                "chat_id": "chat-1",
                "chat_name": "Work",
                "chat_type": "group",
                "sender_id": "sender-1",
                "sender_name": "Alex",
                "is_forwarded": 1,
                "forward_sender_id": "sender-2",
                "forward_sender_name": "Original Alex",
                "forward_date": 1780271000,
                "forward_chat_id": "source-chat",
                "forward_message_id": "source-message",
                "text": "First update",
                "timestamp": 1780272000,
                "source": "telegram",
            },
            {
                "id": 11,
                "message_id": "m11",
                "chat_id": "chat-1",
                "chat_name": "Work",
                "chat_type": "group",
                "sender_id": "sender-1",
                "sender_name": "Alex",
                "is_forwarded": 0,
                "forward_sender_id": None,
                "forward_sender_name": None,
                "forward_date": None,
                "forward_chat_id": None,
                "forward_message_id": None,
                "text": "Second update",
                "timestamp": 1780275600,
                "source": "telegram",
            },
        ]
        with patch("utils.auth.settings") as mock_settings:
            mock_settings.api_key = ""
            with patch("routers.agent.fetch_all", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = rows
                response = client.post(
                    "/api/agent/messages/query",
                    json=_range_payload(
                        chat_ids=["chat-1"],
                        sender_names=["Alex"],
                        text_query="update",
                        limit=2,
                    ),
                )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert data["next_cursor"] == "11"
        assert data["messages"][0]["chat_name"] == "Work"
        assert data["messages"][0]["is_forwarded"] is True
        assert data["messages"][0]["forward_sender_name"] == "Original Alex"
        assert data["messages"][0]["datetime"].startswith("2026-06-01T")

        sql = mock_fetch.call_args.args[0]
        params = mock_fetch.call_args.args[1]
        assert "m.chat_id IN (?)" in sql
        assert "m.sender_name IN (?)" in sql
        assert "LOWER(m.text) LIKE ?" in sql
        assert "COALESCE(c.included, 1) = 1" in sql
        assert "chat-1" in params
        assert "Alex" in params
        assert "%update%" in params

    def test_list_chats_returns_filter_metadata(self):
        rows = [
            {
                "chat_id": "chat-1",
                "chat_name": "Work",
                "chat_type": "group",
                "included": 1,
                "message_count": 12,
                "last_message_at": 1780272000,
                "created_at": 1780000000,
            }
        ]
        with patch("utils.auth.settings") as mock_settings:
            mock_settings.api_key = ""
            with patch("routers.agent.fetch_all", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = rows
                response = client.get("/api/agent/chats?included_only=true")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["chats"][0]["chat_id"] == "chat-1"
        assert data["chats"][0]["included"] is True

    def test_query_chunks_parses_participants_and_can_omit_content(self):
        rows = [
            {
                "id": 3,
                "chunk_id": "chunk-3",
                "chat_id": "chat-1",
                "chat_name": "Work",
                "chat_type": "group",
                "participants": '["Person A", "Person B"]',
                "timestamp_start": 1780272000,
                "timestamp_end": 1780275600,
                "message_count": 5,
                "content": "chunk content",
            }
        ]
        with patch("utils.auth.settings") as mock_settings:
            mock_settings.api_key = ""
            with patch("routers.agent.fetch_all", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = rows
                response = client.post(
                    "/api/agent/chunks/query",
                    json=_range_payload(sender_names=["Alex"], include_content=False),
                )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["chunks"][0]["participants"] == ["Person A", "Person B"]
        assert data["chunks"][0]["content"] is None
        assert "ch.participants LIKE ?" in mock_fetch.call_args.args[0]

    def test_summary_uses_matching_messages_and_llm(self):
        rows = [
            {
                "id": 1,
                "message_id": "m1",
                "chat_id": "chat-1",
                "chat_name": "Work",
                "chat_type": "group",
                "sender_id": "sender-1",
                "sender_name": "Alex",
                "is_forwarded": 0,
                "forward_sender_id": None,
                "forward_sender_name": None,
                "forward_date": None,
                "forward_chat_id": None,
                "forward_message_id": None,
                "text": "We shipped the MVP.",
                "timestamp": 1780272000,
                "source": "telegram",
            }
        ]

        class FakeClient:
            async def stream_chat(self, messages):
                yield "Shipped MVP."

        with patch("utils.auth.settings") as mock_settings:
            mock_settings.api_key = ""
            with patch("routers.agent.fetch_all", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = rows
                with patch(
                    "routers.agent.get_llm_client", return_value=FakeClient()
                ) as mock_get_llm_client:
                    response = client.post(
                        "/api/agent/summary",
                        json=_range_payload(include_messages=True),
                    )

        assert response.status_code == 200
        data = response.json()
        assert data["summary"] == "Shipped MVP."
        assert data["message_count"] == 1
        assert data["messages"][0]["text"] == "We shipped the MVP."
        assert mock_get_llm_client.call_count == 1
        assert mock_get_llm_client.call_args.kwargs == {"enable_thinking": False}

    def test_agent_openapi_is_filtered(self):
        response = client.get("/api/agent/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "/api/agent/messages/query" in data["paths"]
        assert "/api/agent/summary" in data["paths"]
        assert "/api/settings" not in data["paths"]
        assert "/api/reindex" not in data["paths"]
        assert data["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"
