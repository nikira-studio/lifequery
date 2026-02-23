"""Unit tests for RAG query pipeline."""

import pytest
from chunker.chunker import estimate_tokens
from rag.pipeline import SYSTEM_PROMPT, fmt_date, rag_stream_query
from vector_store.chroma import RetrievedChunk


class TestRAGPipeline:
    """Tests for the RAG query pipeline."""

    @pytest.mark.asyncio
    async def test_successful_rag_query(self):
        """Test a complete RAG query with retrieved chunks and streaming response."""
        # Setup test data
        query_text = "What did I talk about last November?"
        chunks = [
            RetrievedChunk(
                chunk_id="chunk1",
                chat_id="chat123",
                chat_name="Work Group",
                participants=["Alice", "Bob", "Charlie"],
                timestamp_start=1699000000,  # Nov 2023
                timestamp_end=1699500000,
                message_count=5,
                content="[2023-11-03 10:00] Alice: Meeting at 2pm\n[2023-11-03 10:05] Bob: Got it",
                distance=0.15,
            ),
            RetrievedChunk(
                chunk_id="chunk2",
                chat_id="chat456",
                chat_name="Family Chat",
                participants=["Mom", "Dad"],
                timestamp_start=1699600000,
                timestamp_end=1699700000,
                message_count=3,
                content="[2023-11-04 15:00] Mom: Don't forget dinner",
                distance=0.25,
            ),
        ]

        async def mock_embed_single(query):
            return [0.1, 0.2, 0.3]

        async def mock_stream_chat(messages):
            for token in ["Hello", " world", "!"]:
                yield token

        # Test with mocks
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("rag.pipeline.embed_single", mock_embed_single)
            mp.setattr("rag.pipeline.query", lambda *args, **kwargs: chunks)
            mp.setattr(
                "rag.pipeline.get_llm_client",
                lambda *args: type(
                    "MockClient", (), {"stream_chat": mock_stream_chat}
                )(),
            )

            events = []
            async for event in rag_stream_query(query_text):
                events.append(event)

            # Verify events
            assert len(events) == 4
            assert events[0]["type"] == "token"
            assert events[0]["content"] == "Hello"
            assert events[1]["type"] == "token"
            assert events[1]["content"] == " world"
            assert events[2]["type"] == "token"
            assert events[2]["content"] == "!"
            assert events[3]["type"] == "citations"
            assert len(events[3]["citations"]) == 2

    @pytest.mark.asyncio
    async def test_empty_results_no_relevant_chunks(self):
        """Test RAG query when no relevant chunks are found."""
        query_text = "What about Mars colonization?"

        # Mock embed_single to return empty results
        with patch("rag.pipeline.embed_single", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1, 0.2, 0.3]

            # Mock query to return empty list
            with patch("rag.pipeline.query", return_value=[]):
                # Mock get_llm_client
                with patch("rag.pipeline.get_llm_client") as mock_client_factory:
                    mock_client = AsyncMock()
                    mock_client.stream_chat = AsyncMock(
                        return_value=make_async_generator(
                            ["No relevant context found."]
                        )
                    )
                    mock_client_factory.return_value = mock_client

                    # Execute
                    events = []
                    async for event in rag_stream_query(query_text):
                        events.append(event)

                    # Verify we still get a response but with no citations
                    assert len(events) == 2
                    assert events[0]["type"] == "token"
                    assert events[0]["content"] == "No relevant context found."
                    assert events[1]["type"] == "citations"
                    assert events[1]["citations"] == []

    @pytest.mark.asyncio
    async def test_context_cap_limit(self):
        """Test that context is limited by context_cap setting."""
        # Create chunks that would exceed a small context cap
        long_content = "A" * 5000  # Long content
        chunks = [
            RetrievedChunk(
                chunk_id="chunk1",
                chat_id="chat1",
                chat_name="Chat 1",
                participants=["User"],
                timestamp_start=1699000000,
                timestamp_end=1699500000,
                message_count=1,
                content=long_content,
                distance=0.1,
            ),
            RetrievedChunk(
                chunk_id="chunk2",
                chat_id="chat2",
                chat_name="Chat 2",
                participants=["User"],
                timestamp_start=1699600000,
                timestamp_end=1699700000,
                message_count=1,
                content="Short content",
                distance=0.2,
            ),
        ]

        # Mock settings with small context cap
        with patch("rag.pipeline.settings") as mock_settings:
            mock_settings.top_k = 10
            mock_settings.context_cap = 1000  # Small cap

            with patch(
                "rag.pipeline.embed_single", new_callable=AsyncMock
            ) as mock_embed:
                mock_embed.return_value = [0.1, 0.2, 0.3]

                with patch("rag.pipeline.query", return_value=chunks):
                    with patch("rag.pipeline.get_llm_client") as mock_client_factory:
                        mock_client = AsyncMock()
                        mock_client.stream_chat = AsyncMock(
                            return_value=make_async_generator(["Response"])
                        )
                        mock_client_factory.return_value = mock_client

                        # Execute
                        events = []
                        async for event in rag_stream_query("test"):
                            events.append(event)

                        # Should only include first chunk (second exceeds cap)
                        assert len(events) == 2
                        assert events[1]["type"] == "citations"
                        assert len(events[1]["citations"]) == 1
                        assert events[1]["citations"][0]["chat_name"] == "Chat 1"

    @pytest.mark.asyncio
    async def test_conversation_history_included(self):
        """Test that conversation history is included in the LLM call."""
        query_text = "What did you say earlier?"
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        chunks = [
            RetrievedChunk(
                chunk_id="chunk1",
                chat_id="chat1",
                chat_name="Test Chat",
                participants=["User"],
                timestamp_start=1699000000,
                timestamp_end=1699500000,
                message_count=1,
                content="Test content",
                distance=0.1,
            )
        ]

        with patch("rag.pipeline.embed_single", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1, 0.2, 0.3]

            with patch("rag.pipeline.query", return_value=chunks):
                with patch("rag.pipeline.get_llm_client") as mock_client_factory:
                    mock_client = AsyncMock()
                    mock_client.stream_chat = AsyncMock(
                        return_value=make_async_generator(["Response"])
                    )
                    mock_client_factory.return_value = mock_client

                    # Execute
                    events = []
                    async for event in rag_stream_query(
                        query_text, conversation_history=history
                    ):
                        events.append(event)

                    # Verify the client was called with history
                    mock_client.stream_chat.assert_called_once()
                    call_args = mock_client.stream_chat.call_args
                    messages = call_args[0][0]

                    # Check that history is included
                    assert len(messages) == 4  # system + 2 history + user query
                    assert messages[0]["role"] == "system"
                    assert messages[1]["role"] == "user"
                    assert messages[1]["content"] == "Hello"
                    assert messages[2]["role"] == "assistant"
                    assert messages[2]["content"] == "Hi there!"
                    assert messages[3]["role"] == "user"
                    assert messages[3]["content"] == query_text

    @pytest.mark.asyncio
    async def test_error_handling_embedding_fails(self):
        """Test that errors during embedding are handled gracefully."""
        with patch("rag.pipeline.embed_single", new_callable=AsyncMock) as mock_embed:
            mock_embed.side_effect = Exception("Embedding failed")

            # Execute
            events = []
            async for event in rag_stream_query("test"):
                events.append(event)

            # Should yield error event
            assert len(events) == 1
            assert events[0]["type"] == "error"
            assert "Embedding failed" in events[0]["message"]

    @pytest.mark.asyncio
    async def test_error_handling_llm_fails(self):
        """Test that errors during LLM streaming are handled gracefully."""
        chunks = [
            RetrievedChunk(
                chunk_id="chunk1",
                chat_id="chat1",
                chat_name="Test Chat",
                participants=["User"],
                timestamp_start=1699000000,
                timestamp_end=1699500000,
                message_count=1,
                content="Test content",
                distance=0.1,
            )
        ]

        with patch("rag.pipeline.embed_single", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1, 0.2, 0.3]

            with patch("rag.pipeline.query", return_value=chunks):
                with patch("rag.pipeline.get_llm_client") as mock_client_factory:
                    mock_client = AsyncMock()
                    mock_client.stream_chat = AsyncMock(
                        side_effect=Exception("LLM failed")
                    )
                    mock_client_factory.return_value = mock_client

                    # Execute
                    events = []
                    async for event in rag_stream_query("test"):
                        events.append(event)

                    # Should yield error event
                    assert len(events) == 1
                    assert events[0]["type"] == "error"
                    assert "LLM failed" in events[0]["message"]

    @pytest.mark.asyncio
    async def test_no_chunks_fit_context_cap(self):
        """Test when first chunk alone exceeds context cap."""
        # Create a single huge chunk
        huge_content = "A" * 10000
        chunks = [
            RetrievedChunk(
                chunk_id="chunk1",
                chat_id="chat1",
                chat_name="Test Chat",
                participants=["User"],
                timestamp_start=1699000000,
                timestamp_end=1699500000,
                message_count=1,
                content=huge_content,
                distance=0.1,
            )
        ]

        with patch("rag.pipeline.settings") as mock_settings:
            mock_settings.top_k = 10
            mock_settings.context_cap = 100  # Very small cap

            with patch(
                "rag.pipeline.embed_single", new_callable=AsyncMock
            ) as mock_embed:
                mock_embed.return_value = [0.1, 0.2, 0.3]

                with patch("rag.pipeline.query", return_value=chunks):
                    # Execute
                    events = []
                    async for event in rag_stream_query("test"):
                        events.append(event)

                    # Should yield error about no context fitting
                    assert len(events) == 1
                    assert events[0]["type"] == "error"
                    assert "token limit" in events[0]["message"]

    def test_system_prompt_formatting(self):
        """Test that system prompt is properly formatted with context."""
        context_text = "Sample conversation\n---\nAnother conversation"
        formatted = SYSTEM_PROMPT.format(context_text=context_text)

        assert "You are LifeQuery" in formatted
        assert context_text in formatted
        assert "Answer only from the provided context" in formatted

    def test_fmt_date(self):
        """Test date formatting function."""
        # Test various timestamps
        assert fmt_date(1699000000) == "2023-11-03"
        assert fmt_date(0) == "1970-01-01"
        assert fmt_date(946684800) == "2000-01-01"

    def test_estimate_tokens_integration(self):
        """Test that estimate_tokens works correctly with RAG context."""
        text = "Hello world this is a test"
        tokens = estimate_tokens(text)
        # 5 words * 1.35 = 6.75 -> 6 (int)
        assert tokens == 6

    @pytest.mark.asyncio
    async def test_multiple_chunks_in_context(self):
        """Test multiple chunks fit within context cap."""
        chunks = [
            RetrievedChunk(
                chunk_id=f"chunk{i}",
                chat_id="chat1",
                chat_name=f"Chat {i}",
                participants=["User"],
                timestamp_start=1699000000 + i * 1000,
                timestamp_end=1699500000 + i * 1000,
                message_count=1,
                content=f"Short content {i}",
                distance=0.1 + i * 0.05,
            )
            for i in range(5)
        ]

        with patch("rag.pipeline.settings") as mock_settings:
            mock_settings.top_k = 10
            mock_settings.context_cap = 5000  # Should fit all

            with patch(
                "rag.pipeline.embed_single", new_callable=AsyncMock
            ) as mock_embed:
                mock_embed.return_value = [0.1, 0.2, 0.3]

                with patch("rag.pipeline.query", return_value=chunks):
                    with patch("rag.pipeline.get_llm_client") as mock_client_factory:
                        mock_client = AsyncMock()
                        mock_client.stream_chat = AsyncMock(
                            return_value=make_async_generator(["Response"])
                        )
                        mock_client_factory.return_value = mock_client

                        # Execute
                        events = []
                        async for event in rag_stream_query("test"):
                            events.append(event)

                        # All chunks should be included
                        assert events[-1]["type"] == "citations"
                        assert len(events[-1]["citations"]) == 5
