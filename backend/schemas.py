"""Pydantic schemas for LifeQuery API request/response models."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

# ============================================================================
# Health
# ============================================================================


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str = "1.0.0"


# ============================================================================
# Settings
# ============================================================================


class SettingsResponse(BaseModel):
    """Settings response with sensitive fields masked."""

    telegram_api_id: str
    telegram_api_hash: str
    telegram_fetch_batch: int
    telegram_fetch_wait: int
    ollama_url: str
    embedding_model: str
    chat_provider: str
    chat_model: str
    chat_url: str
    chat_api_key: str
    openrouter_api_key: str
    custom_chat_url: str
    temperature: float
    max_tokens: int
    top_k: int
    context_cap: int
    chunk_target: int
    chunk_max: int
    chunk_overlap: int
    api_key: str = ""
    auto_sync_interval: int = 30
    enable_thinking: bool = False
    enable_rag: bool = True
    system_prompt: str = ""
    user_first_name: str = ""
    user_last_name: str = ""
    user_username: str = ""
    noise_filter_keywords: str = ""


class SettingsUpdate(BaseModel):
    """Settings update request - all fields optional."""

    telegram_api_id: str | None = None
    telegram_api_hash: str | None = None
    telegram_fetch_batch: int | None = None
    telegram_fetch_wait: int | None = None
    ollama_url: str | None = None
    embedding_model: str | None = None
    chat_provider: str | None = None
    chat_model: str | None = None
    chat_url: str | None = None
    chat_api_key: str | None = None
    openrouter_api_key: str | None = None
    custom_chat_url: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_k: int | None = None
    context_cap: int | None = None
    chunk_target: int | None = None
    chunk_max: int | None = None
    chunk_overlap: int | None = None
    api_key: str | None = None
    auto_sync_interval: int | None = None
    enable_thinking: bool | None = None
    enable_rag: bool | None = None
    system_prompt: str | None = None
    noise_filter_keywords: str | None = None


class SettingsUpdateResponse(BaseModel):
    """Settings update response."""

    ok: bool = True


# ============================================================================
# Stats
# ============================================================================


class StatsResponse(BaseModel):
    """Database statistics response."""

    message_count: int
    chunk_count: int
    chat_count: int
    included_chat_count: int = 0
    excluded_chat_count: int = 0
    embedded_count: int
    last_sync: datetime | None = None
    last_sync_added: int = 0


# ============================================================================
# Telegram Auth
# ============================================================================


class TelegramStatusResponse(BaseModel):
    """Telegram connection status response."""

    state: str = Field(
        description="One of: uninitialized, needs_auth, phone_sent, connected"
    )
    detail: str | None = Field(
        default=None, description="Error message if state is error"
    )


class PhoneRequest(BaseModel):
    """Phone number for Telegram auth."""

    phone: str = Field(
        ..., description="Phone number with country code, e.g., +12125551234"
    )


class PhoneSentResponse(BaseModel):
    """Response after phone number is sent."""

    state: str = "phone_sent"


class VerifyRequest(BaseModel):
    """Verification code request.

    For normal authentication (non-2FA): provide phone and code
    For 2FA authentication: provide token and code (phone is optional)
    """

    phone: str | None = Field(
        default=None, description="Phone number used for auth (for non-2FA)"
    )
    code: str = Field(..., description="Verification code received via SMS/app")
    token: str | None = Field(
        default=None,
        description="Auth token returned from /auth/start (required for 2FA accounts)",
    )
    password: str | None = Field(
        default=None, description="Two-step verification password (optional)"
    )


class ConnectedResponse(BaseModel):
    """Response after successful verification."""

    state: str = "connected"
    token: str | None = Field(
        default=None, description="Auth token (for 2FA accounts only)"
    )


class PhoneSentErrorResponse(BaseModel):
    """Response when verification code is wrong."""

    state: str = "phone_sent"
    error: str = Field(..., description="Error message describing the failure")


class NeedsAuthResponse(BaseModel):
    """Response after disconnecting."""

    state: str = "needs_auth"


# ============================================================================
# Data Operations
# ============================================================================


class ReindexRequest(BaseModel):
    """Reindex request - requires explicit confirmation."""

    confirm: bool = Field(..., description="Must be true to proceed with reindex")


class ImportPathRequest(BaseModel):
    """Request to import from a local JSON file path."""

    path: str = Field(..., description="Absolute path to the JSON file on the server")
    username: Optional[str] = Field(None, description="Optional username to use for attribution")


class SyncLogEntry(BaseModel):
    """Sync log entry."""

    id: int
    operation: str
    started_at: int
    finished_at: Optional[int] = None
    status: Optional[str] = None
    messages_added: int = 0
    chunks_created: int = 0
    skipped_duplicate: int = 0
    skipped_empty: int = 0
    detail: Optional[str] = None


class SyncLogResponse(BaseModel):
    """Sync log response."""

    logs: list[SyncLogEntry]


# ============================================================================
# Chat
# ============================================================================


class ChatUpdateRequest(BaseModel):
    """Request to update chat inclusion status."""

    included: bool = Field(
        ..., description="Whether to include (true) or exclude (false) the chat"
    )


class Message(BaseModel):
    """Chat message."""

    role: str = Field(..., description="Message role: 'user', 'assistant', or 'system'")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    """Chat request with conversation history."""

    messages: list[Message] = Field(
        ..., description="List of messages in the conversation"
    )


# ============================================================================
# SSE Events
# ============================================================================


class ProgressEvent(BaseModel):
    """Progress update event during sync/import/reindex."""

    type: str = "progress"
    stage: str = Field(..., description="Operation stage: ingest, import, chunk, embed")
    message: str = Field(..., description="Human-readable progress message")


class DoneEvent(BaseModel):
    """Operation complete event."""

    type: str = "done"
    messages_added: int = 0
    chunks_created: int = 0
    chunks_embedded: int = 0


class ErrorEvent(BaseModel):
    """Error event during operation."""

    type: str = "error"
    message: str = Field(..., description="Error message")


class TokenEvent(BaseModel):
    """Token event during chat streaming."""

    type: str = "token"
    content: str = Field(..., description="Token content")


class Citation(BaseModel):
    """Citation information for a chunk."""

    chat_name: str = Field(..., description="Name of the chat")
    date_range: str = Field(
        ..., description="Date range of the chunk, e.g., 'Nov 3–12, 2025'"
    )
    participants: list[str] = Field(..., description="List of participants in the chat")


class CitationsEvent(BaseModel):
    """Citations event after chat completion."""

    type: str = "citations"
    citations: list[Citation] = Field(
        ..., description="List of citations for used chunks"
    )


# ============================================================================
# OpenAI-Compatible Endpoint
# ============================================================================


class OpenAIMessage(BaseModel):
    """OpenAI-compatible message format."""

    role: str = Field(..., description="Message role: 'system', 'user', or 'assistant'")
    content: str = Field(..., description="Message content")


class OpenAIChatRequest(BaseModel):
    """OpenAI-compatible chat completion request."""

    model: Optional[str] = Field(
        default=None,
        description="Model name (ignored - uses configured model)",
    )
    messages: list[OpenAIMessage] = Field(
        ..., description="List of messages in the conversation"
    )
    stream: Optional[bool] = Field(
        default=True,
        description="Whether to stream the response",
    )
    temperature: Optional[float] = Field(
        default=None,
        description="Sampling temperature (overrides config if provided)",
    )
    max_tokens: Optional[int] = Field(
        default=None,
        description="Maximum tokens to generate (overrides config if provided)",
    )
    rag: Optional[bool] = Field(
        default=None,
        description="Whether to enable memory retrieval (RAG) for this request. If not provided, uses the global system setting.",
    )
    thinking: Optional[bool] = Field(
        default=None,
        description="Whether to enable model thinking/reasoning tags for this request.",
    )
    enable_thinking: Optional[bool] = Field(
        default=None,
        description="Alias for thinking (per-request override).",
    )


class OpenAIUsage(BaseModel):
    """OpenAI usage information."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class OpenAIDelta(BaseModel):
    """OpenAI delta for streaming responses."""

    content: Optional[str] = Field(
        default=None,
        description="Delta content for streaming",
    )


class OpenAIChoice(BaseModel):
    """OpenAI completion choice."""

    index: int = 0
    delta: Optional[OpenAIDelta] = None
    message: Optional[OpenAIMessage] = None
    finish_reason: Optional[str] = None


class OpenAIChatResponse(BaseModel):
    """OpenAI-compatible chat completion response (non-streaming)."""

    id: str = Field(
        default_factory=lambda: f"chatcmpl-{datetime.now().timestamp()}",
        description="Chat completion ID",
    )
    object: str = "chat.completion"
    model: str = "lifequery"
    choices: list[OpenAIChoice]
    usage: Optional[OpenAIUsage] = None
    x_citations: Optional[list[Citation]] = Field(
        default=None,
        description="LifeQuery-specific citations field",
    )
