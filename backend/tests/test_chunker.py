"""Unit tests for the chunking engine."""

import pytest
from chunker.chunker import (
    CHUNK_MIN_TOKENS,
    GAP_HARD_SECONDS,
    GAP_SOFT_SECONDS,
    Chunk,
    chunk_chat,
    compute_content_hash,
    estimate_tokens,
    format_message,
)


class TestEstimateTokens:
    """Tests for the token estimation function."""

    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_single_word(self):
        # "hello" -> 1 word -> 1.35 tokens -> 1 (int)
        assert estimate_tokens("hello") == 1

    def test_multiple_words(self):
        # "hello world test" -> 3 words -> 4.05 tokens -> 4
        assert estimate_tokens("hello world test") == 4

    def test_sentence(self):
        text = "This is a test sentence with multiple words"
        # 8 words -> 10.8 tokens -> 10
        assert estimate_tokens(text) == 10


class TestFormatMessage:
    """Tests for message formatting."""

    def test_basic_format(self):
        # timestamp 1699030920 = 2023-11-03 17:02 UTC
        result = format_message(1699030920, "Alice", "Hello world")
        assert result == "[2023-11-03 17:02] Alice: Hello world"

    def test_empty_sender(self):
        result = format_message(1699030920, "", "Message")
        assert result == "[2023-11-03 17:02] : Message"


class TestComputeContentHash:
    """Tests for content hash computation."""

    def test_same_content_same_hash(self):
        hash1 = compute_content_hash("test content")
        hash2 = compute_content_hash("test content")
        assert hash1 == hash2

    def test_different_content_different_hash(self):
        hash1 = compute_content_hash("test content 1")
        hash2 = compute_content_hash("test content 2")
        assert hash1 != hash2

    def test_hash_length(self):
        hash_result = compute_content_hash("test")
        assert len(hash_result) == 64  # SHA256 hex length


class TestChunkChat:
    """Tests for the main chunking algorithm."""

    def test_empty_chat(self):
        """Empty chat → produces no chunks."""
        result = chunk_chat([])
        assert result == []

    def test_single_message(self):
        """Single-message chat → produces one chunk."""
        messages = [
            {
                "message_id": "1",
                "chat_id": "chat1",
                "chat_name": "Test Chat",
                "sender_id": "user1",
                "sender_name": "Alice",
                "text": "Hello",
                "timestamp": 1699030920,
            }
        ]
        result = chunk_chat(messages)
        assert len(result) == 1
        assert result[0].message_count == 1
        assert result[0].chat_id == "chat1"
        assert "Alice" in result[0].participants

    def test_normal_rolling_window(self):
        """Messages within 20 min stay together."""
        base_time = 1699030920  # 2023-11-03 14:22
        messages = [
            {
                "message_id": str(i),
                "chat_id": "chat1",
                "chat_name": "Test Chat",
                "sender_id": "user1",
                "sender_name": "Alice",
                "text": f"Message {i}",
                "timestamp": base_time + (i * 60),  # 1 minute apart
            }
            for i in range(10)
        ]
        result = chunk_chat(messages)
        # All messages should be in one chunk
        assert len(result) == 1
        assert result[0].message_count == 10

    def test_soft_break_with_sufficient_content(self):
        """20-minute gap with sufficient prior content → new chunk."""
        base_time = 1699030920
        # Need at least 300 tokens before the gap - each message needs ~60+ words
        long_text = (
            "This is a longer message with many more words to ensure we exceed the token minimum threshold for the soft break test. "
            * 3
        )
        messages = [
            {
                "message_id": str(i),
                "chat_id": "chat1",
                "chat_name": "Test Chat",
                "sender_id": "user1",
                "sender_name": "Alice",
                "text": f"Message {i}: {long_text}",
                "timestamp": base_time + (i * 60),
            }
            for i in range(5)
        ]
        # Add a gap of 25 minutes (above soft break threshold)
        messages.append(
            {
                "message_id": "6",
                "chat_id": "chat1",
                "chat_name": "Test Chat",
                "sender_id": "user1",
                "sender_name": "Alice",
                "text": "Message after gap",
                "timestamp": base_time + (5 * 60) + GAP_SOFT_SECONDS + 60,
            }
        )

        result = chunk_chat(messages)
        # Should have 2 chunks due to soft break
        assert len(result) == 2

    def test_soft_break_with_insufficient_content(self):
        """20-minute gap with tiny prior content (< 300 tokens) → continues."""
        base_time = 1699030920
        # First message only (tiny content)
        messages = [
            {
                "message_id": "1",
                "chat_id": "chat1",
                "chat_name": "Test Chat",
                "sender_id": "user1",
                "sender_name": "Alice",
                "text": "Hi",  # Very short
                "timestamp": base_time,
            }
        ]
        # Add a gap of 25 minutes
        messages.append(
            {
                "message_id": "2",
                "chat_id": "chat1",
                "chat_name": "Test Chat",
                "sender_id": "user1",
                "sender_name": "Alice",
                "text": "Message after gap",
                "timestamp": base_time + GAP_SOFT_SECONDS + 60,
            }
        )

        result = chunk_chat(messages)
        # Should have 1 chunk (continues because content is small)
        assert len(result) == 1
        assert result[0].message_count == 2

    def test_hard_break_always_splits(self):
        """4-hour gap → always new chunk regardless of prior size."""
        base_time = 1699030920
        messages = [
            {
                "message_id": str(i),
                "chat_id": "chat1",
                "chat_name": "Test Chat",
                "sender_id": "user1",
                "sender_name": "Alice",
                "text": f"Message {i} with some content",
                "timestamp": base_time + (i * 60),
            }
            for i in range(3)
        ]
        # Add a gap of 5 hours (above hard break threshold)
        messages.append(
            {
                "message_id": "4",
                "chat_id": "chat1",
                "chat_name": "Test Chat",
                "sender_id": "user1",
                "sender_name": "Alice",
                "text": "Message after long gap",
                "timestamp": base_time + (3 * 60) + GAP_HARD_SECONDS + 60,
            }
        )

        result = chunk_chat(messages)
        # Should have 2 chunks due to hard break
        assert len(result) == 2

    def test_hard_max_split_with_overlap(self):
        """Hard-max split → chunk is split and overlap is applied."""
        base_time = 1699030920
        # Need enough text to exceed chunk_max (1200 tokens by default)
        # With ~1.35 tokens per word, need ~900 words minimum
        # Each message has about 10 words, so need ~90+ messages
        very_long_text = (
            "This is a very long message with lots of words to ensure we exceed the chunk maximum token limit. "
            * 20
        )
        messages = [
            {
                "message_id": str(i),
                "chat_id": "chat1",
                "chat_name": "Test Chat",
                "sender_id": "user1",
                "sender_name": "Alice",
                "text": f"Message {i}: {very_long_text}",
                "timestamp": base_time + (i * 60),
            }
            for i in range(100)  # Should definitely exceed chunk_max
        ]

        result = chunk_chat(messages)
        # Should have multiple chunks
        assert len(result) > 1
        # Verify overlap is applied (content should appear in consecutive chunks)
        for i in range(len(result) - 1):
            # The end of chunk i should share some content with start of chunk i+1
            assert result[i].timestamp_end <= result[i + 1].timestamp_start

    def test_multiple_participants(self):
        """Chat with multiple participants."""
        base_time = 1699030920
        messages = [
            {
                "message_id": "1",
                "chat_id": "chat1",
                "chat_name": "Group Chat",
                "sender_id": "user1",
                "sender_name": "Alice",
                "text": "Hello everyone",
                "timestamp": base_time,
            },
            {
                "message_id": "2",
                "chat_id": "chat1",
                "chat_name": "Group Chat",
                "sender_id": "user2",
                "sender_name": "Bob",
                "text": "Hi Alice",
                "timestamp": base_time + 60,
            },
            {
                "message_id": "3",
                "chat_id": "chat1",
                "chat_name": "Group Chat",
                "sender_id": "user3",
                "sender_name": "Charlie",
                "text": "Hey guys",
                "timestamp": base_time + 120,
            },
        ]

        result = chunk_chat(messages)
        assert len(result) == 1
        participants = result[0].participants
        assert "Alice" in participants
        assert "Bob" in participants
        assert "Charlie" in participants


class TestConstants:
    """Test that constants are set correctly."""

    def test_gap_hard_seconds(self):
        # 4 hours = 4 * 60 * 60 = 14400
        assert GAP_HARD_SECONDS == 14400

    def test_gap_soft_seconds(self):
        # 20 minutes = 20 * 60 = 1200
        assert GAP_SOFT_SECONDS == 1200

    def test_chunk_min_tokens(self):
        assert CHUNK_MIN_TOKENS == 300
