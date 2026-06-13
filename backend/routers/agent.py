"""Agent-facing data access API.

This router exposes a narrow, authenticated surface for external agents and
OpenAPI-based connector systems. It intentionally excludes UI/admin operations
such as sync, import, reindex, settings, and Telegram authentication.
"""

from datetime import datetime, timezone
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.openapi.utils import get_openapi

from config import settings
from db.database import fetch_all
from llm.client import get_llm_client
from schemas import (
    AgentChatListResponse,
    AgentChatRecord,
    AgentChunkQueryRequest,
    AgentChunkQueryResponse,
    AgentChunkRecord,
    AgentMessageQueryResponse,
    AgentMessageRecord,
    AgentPersonListResponse,
    AgentPersonRecord,
    AgentQueryFilters,
    AgentSummaryRequest,
    AgentSummaryResponse,
)
from utils.auth import verify_api_key
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _to_epoch(value: datetime) -> int:
    """Convert a Pydantic datetime to a Unix timestamp."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def _to_datetime(timestamp: int) -> datetime:
    """Convert a Unix timestamp to an aware UTC datetime."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _decode_cursor(cursor: str | None) -> int | None:
    if not cursor:
        return None
    try:
        value = int(cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    if value < 0:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return value


def _add_in_filter(
    clauses: list[str],
    params: list[Any],
    column: str,
    values: list[str] | None,
) -> None:
    if not values:
        return
    placeholders = ", ".join(["?"] * len(values))
    clauses.append(f"{column} IN ({placeholders})")
    params.extend(values)


def _build_message_where(request: AgentQueryFilters) -> tuple[str, list[Any]]:
    start_ts = _to_epoch(request.start)
    end_ts = _to_epoch(request.end)
    clauses = ["m.timestamp >= ?", "m.timestamp < ?"]
    params: list[Any] = [start_ts, end_ts]

    _add_in_filter(clauses, params, "m.chat_id", request.chat_ids)
    _add_in_filter(clauses, params, "m.chat_name", request.chat_names)
    _add_in_filter(clauses, params, "c.chat_type", request.chat_types)
    _add_in_filter(clauses, params, "m.sender_id", request.sender_ids)
    _add_in_filter(clauses, params, "m.sender_name", request.sender_names)
    _add_in_filter(clauses, params, "m.source", request.sources)

    if request.included_only:
        clauses.append("COALESCE(c.included, 1) = 1")

    if request.text_query:
        clauses.append("LOWER(m.text) LIKE ?")
        params.append(f"%{request.text_query.lower()}%")

    cursor = _decode_cursor(request.cursor)
    if cursor is not None:
        if request.order == "desc":
            clauses.append("m.id < ?")
        else:
            clauses.append("m.id > ?")
        params.append(cursor)

    return " AND ".join(clauses), params


def _message_from_row(row: dict) -> AgentMessageRecord:
    return AgentMessageRecord(
        id=row["id"],
        message_id=row["message_id"],
        chat_id=row["chat_id"],
        chat_name=row["chat_name"],
        chat_type=row["chat_type"],
        sender_id=row["sender_id"],
        sender_name=row["sender_name"],
        text=row["text"],
        timestamp=row["timestamp"],
        datetime=_to_datetime(row["timestamp"]),
        source=row["source"],
    )


async def _query_messages(
    request: AgentQueryFilters,
) -> tuple[list[AgentMessageRecord], str | None]:
    where_sql, params = _build_message_where(request)
    order_sql = "DESC" if request.order == "desc" else "ASC"
    rows = await fetch_all(
        f"""
        SELECT
            m.id, m.message_id, m.chat_id, m.chat_name,
            c.chat_type, m.sender_id, m.sender_name, m.text,
            m.timestamp, m.source
        FROM messages m
        LEFT JOIN chats c ON m.chat_id = c.chat_id
        WHERE {where_sql}
        ORDER BY m.id {order_sql}
        LIMIT ?
        """,
        tuple(params + [request.limit]),
    )
    messages = [_message_from_row(row) for row in rows]
    next_cursor = str(messages[-1].id) if len(messages) == request.limit else None
    return messages, next_cursor


@router.get("/chats", response_model=AgentChatListResponse, operation_id="list_chats")
async def list_chats(
    raw_request: Request,
    included_only: bool = Query(default=True),
    chat_type: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
) -> AgentChatListResponse:
    """List chats/groups/channels that can be used as query filters."""
    verify_api_key(raw_request)

    clauses = []
    params: list[Any] = []
    if included_only:
        clauses.append("included = 1")
    if chat_type:
        clauses.append("chat_type = ?")
        params.append(chat_type)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await fetch_all(
        f"""
        SELECT chat_id, chat_name, chat_type, included, message_count,
               last_message_at, created_at
        FROM chats
        {where_sql}
        ORDER BY last_message_at DESC, chat_name ASC
        LIMIT ?
        """,
        tuple(params + [limit]),
    )

    chats = [
        AgentChatRecord(
            chat_id=row["chat_id"],
            chat_name=row["chat_name"],
            chat_type=row["chat_type"],
            included=bool(row["included"]),
            message_count=row["message_count"] or 0,
            last_message_at=row["last_message_at"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
    return AgentChatListResponse(chats=chats, count=len(chats))


@router.get(
    "/people", response_model=AgentPersonListResponse, operation_id="list_people"
)
async def list_people(
    raw_request: Request,
    search: str | None = Query(default=None),
    included_only: bool = Query(default=True),
    limit: int = Query(default=500, ge=1, le=2000),
) -> AgentPersonListResponse:
    """List known senders/participants that can be used as query filters."""
    verify_api_key(raw_request)

    clauses = ["m.sender_name IS NOT NULL", "m.sender_name != ''"]
    params: list[Any] = []
    if included_only:
        clauses.append("COALESCE(c.included, 1) = 1")
    if search:
        clauses.append("LOWER(m.sender_name) LIKE ?")
        params.append(f"%{search.lower()}%")

    rows = await fetch_all(
        f"""
        SELECT
            m.sender_id, m.sender_name, COUNT(*) AS message_count,
            MIN(m.timestamp) AS first_message_at,
            MAX(m.timestamp) AS last_message_at
        FROM messages m
        LEFT JOIN chats c ON m.chat_id = c.chat_id
        WHERE {' AND '.join(clauses)}
        GROUP BY m.sender_id, m.sender_name
        ORDER BY message_count DESC, m.sender_name ASC
        LIMIT ?
        """,
        tuple(params + [limit]),
    )

    people = [
        AgentPersonRecord(
            sender_id=row["sender_id"],
            sender_name=row["sender_name"],
            message_count=row["message_count"] or 0,
            first_message_at=row["first_message_at"],
            last_message_at=row["last_message_at"],
        )
        for row in rows
    ]
    return AgentPersonListResponse(people=people, count=len(people))


@router.post(
    "/messages/query",
    response_model=AgentMessageQueryResponse,
    operation_id="query_messages",
)
async def query_messages(
    request: AgentQueryFilters, raw_request: Request
) -> AgentMessageQueryResponse:
    """Pull raw messages by date range, chat/group, sender, source, and text."""
    verify_api_key(raw_request)
    messages, next_cursor = await _query_messages(request)
    return AgentMessageQueryResponse(
        messages=messages, count=len(messages), next_cursor=next_cursor
    )


def _build_chunk_where(request: AgentChunkQueryRequest) -> tuple[str, list[Any]]:
    start_ts = _to_epoch(request.start)
    end_ts = _to_epoch(request.end)
    clauses = ["ch.timestamp_end >= ?", "ch.timestamp_start < ?"]
    params: list[Any] = [start_ts, end_ts]

    _add_in_filter(clauses, params, "ch.chat_id", request.chat_ids)
    _add_in_filter(clauses, params, "ch.chat_name", request.chat_names)
    _add_in_filter(clauses, params, "c.chat_type", request.chat_types)

    if request.included_only:
        clauses.append("COALESCE(c.included, 1) = 1")

    if request.sender_names:
        participant_clauses = []
        for name in request.sender_names:
            participant_clauses.append("ch.participants LIKE ?")
            params.append(f"%{name}%")
        clauses.append(f"({' OR '.join(participant_clauses)})")

    if request.text_query:
        clauses.append("LOWER(ch.content) LIKE ?")
        params.append(f"%{request.text_query.lower()}%")

    cursor = _decode_cursor(request.cursor)
    if cursor is not None:
        if request.order == "desc":
            clauses.append("ch.id < ?")
        else:
            clauses.append("ch.id > ?")
        params.append(cursor)

    return " AND ".join(clauses), params


def _chunk_from_row(row: dict, include_content: bool) -> AgentChunkRecord:
    try:
        participants = json.loads(row["participants"] or "[]")
    except (json.JSONDecodeError, TypeError):
        participants = []

    return AgentChunkRecord(
        id=row["id"],
        chunk_id=row["chunk_id"],
        chat_id=row["chat_id"],
        chat_name=row["chat_name"],
        chat_type=row["chat_type"],
        participants=participants,
        timestamp_start=row["timestamp_start"],
        timestamp_end=row["timestamp_end"],
        datetime_start=_to_datetime(row["timestamp_start"]),
        datetime_end=_to_datetime(row["timestamp_end"]),
        message_count=row["message_count"] or 0,
        content=row["content"] if include_content else None,
    )


@router.post(
    "/chunks/query",
    response_model=AgentChunkQueryResponse,
    operation_id="query_chunks",
)
async def query_chunks(
    request: AgentChunkQueryRequest, raw_request: Request
) -> AgentChunkQueryResponse:
    """Pull chunked conversation context by date range, chat/group, and person."""
    verify_api_key(raw_request)

    where_sql, params = _build_chunk_where(request)
    order_sql = "DESC" if request.order == "desc" else "ASC"
    rows = await fetch_all(
        f"""
        SELECT
            ch.id, ch.chunk_id, ch.chat_id, ch.chat_name, c.chat_type,
            ch.participants, ch.timestamp_start, ch.timestamp_end,
            ch.message_count, ch.content
        FROM chunks ch
        LEFT JOIN chats c ON ch.chat_id = c.chat_id
        WHERE {where_sql}
        ORDER BY ch.id {order_sql}
        LIMIT ?
        """,
        tuple(params + [request.limit]),
    )
    chunks = [_chunk_from_row(row, request.include_content) for row in rows]
    next_cursor = str(chunks[-1].id) if len(chunks) == request.limit else None
    return AgentChunkQueryResponse(
        chunks=chunks, count=len(chunks), next_cursor=next_cursor
    )


def _format_messages_for_summary(messages: list[AgentMessageRecord]) -> str:
    lines = []
    for message in messages:
        dt = message.datetime.strftime("%Y-%m-%d %H:%M:%S UTC")
        sender = message.sender_name or message.sender_id or "Unknown"
        chat = message.chat_name or message.chat_id
        text = message.text or ""
        lines.append(f"[{dt}] [{chat}] {sender}: {text}")
    return "\n".join(lines)


@router.post(
    "/summary",
    response_model=AgentSummaryResponse,
    operation_id="summarize_range",
)
async def summarize_range(
    request: AgentSummaryRequest, raw_request: Request
) -> AgentSummaryResponse:
    """Generate a concise summary over a filtered raw-message range."""
    verify_api_key(raw_request)

    messages, next_cursor = await _query_messages(request)
    if not messages:
        return AgentSummaryResponse(
            summary="No matching LifeQuery messages were found for that range.",
            message_count=0,
            next_cursor=None,
            messages=[] if request.include_messages else None,
        )

    context = _format_messages_for_summary(messages)
    extra_instruction = f"\nExtra instruction: {request.prompt}" if request.prompt else ""
    prompt = (
        "Summarize the LifeQuery messages below for an agent workflow. "
        "Use only the messages provided. Include concrete dates, people, chats, "
        "decisions, open loops, and notable events when present. "
        "If the evidence is thin, say so plainly."
        f"{extra_instruction}\n\nMESSAGES:\n{context}"
    )

    client = get_llm_client(settings)
    parts = []
    async for token in client.stream_chat([{"role": "user", "content": prompt}]):
        parts.append(token)

    return AgentSummaryResponse(
        summary="".join(parts).strip(),
        message_count=len(messages),
        next_cursor=next_cursor,
        messages=messages if request.include_messages else None,
    )


@router.get("/openapi.json", include_in_schema=False)
async def agent_openapi(raw_request: Request) -> dict:
    """Return an OpenAPI spec containing only the agent-facing endpoints."""
    agent_routes = [
        route
        for route in raw_request.app.routes
        if getattr(route, "path", "").startswith("/api/agent/")
        and getattr(route, "path", "") != "/api/agent/openapi.json"
    ]
    spec = get_openapi(
        title="LifeQuery Agent API",
        version="1.0.0",
        description=(
            "Authenticated LifeQuery data access for agent connectors. "
            "Use Bearer auth with the configured LifeQuery API key."
        ),
        routes=agent_routes,
    )
    spec.setdefault("components", {}).setdefault("securitySchemes", {})[
        "BearerAuth"
    ] = {"type": "http", "scheme": "bearer"}
    for path_item in spec.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation["security"] = [{"BearerAuth": []}]
    return spec
