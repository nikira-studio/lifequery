"""Database models - plain dataclasses mirroring table rows."""

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Message:
    id: Optional[int] = None
    message_id: str = ""
    chat_id: str = ""
    chat_name: Optional[str] = None
    sender_id: Optional[str] = None
    sender_name: Optional[str] = None
    text: Optional[str] = None
    timestamp: int = 0
    source: str = ""
    imported_at: int = field(default_factory=lambda: int(time.time()))


@dataclass
class Chunk:
    id: Optional[int] = None
    chunk_id: str = ""
    chat_id: str = ""
    chat_name: Optional[str] = None
    participants: str = "[]"
    timestamp_start: int = 0
    timestamp_end: int = 0
    message_count: int = 0
    content: str = ""
    content_hash: str = ""
    embedding_version: str = ""
    embedded_at: Optional[int] = None

    def get_participants_list(self) -> list[str]:
        """Get participants as a list (deserializes JSON string)."""
        import json

        try:
            return json.loads(self.participants) if self.participants else []
        except json.JSONDecodeError:
            return []

    def set_participants_list(self, participants: list[str]) -> None:
        """Set participants from a list (serializes to JSON string)."""
        import json

        self.participants = json.dumps(participants)

    @property
    def participants_list(self) -> list[str]:
        """Property alias for get_participants_list()."""
        return self.get_participants_list()


@dataclass
class Config:
    key: str = ""
    value: Optional[str] = None
    updated_at: int = field(default_factory=lambda: int(time.time()))


@dataclass
class Chat:
    chat_id: str = ""
    chat_name: Optional[str] = None
    chat_type: Optional[str] = None  # 'group', 'private', 'channel'
    included: int = 1  # 1 = included, 0 = excluded
    message_count: int = 0
    last_message_at: Optional[int] = None
    created_at: int = field(default_factory=lambda: int(time.time()))


@dataclass
class SyncLog:
    id: Optional[int] = None
    operation: str = ""
    started_at: int = 0
    finished_at: Optional[int] = None
    status: Optional[str] = None
    messages_added: Optional[int] = None
    chunks_created: Optional[int] = None
    detail: Optional[str] = None
