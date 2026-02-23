"""Structured logging module for LifeQuery with rotation and sensitive data filtering."""

import json
import logging
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

from db.database import DATA_DIR

# Sensitive patterns to filter out of logs
SENSITIVE_PATTERNS = [
    r'telegram_api_hash["\s:=]+["\']?[^\s"\']+',
    r'openrouter_api_key["\s:=]+["\']?[^\s"\']+',
    r'session_file["\s:=]+["\']?[^\s"\']+',
    r'code["\s:=]+["\']?\d{5,7}',  # Telegram auth codes
    r'phone["\s:=]+["\']?\+?\d{7,15}',  # Phone numbers
]


def _filter_sensitive_data(message: str) -> str:
    """Filter sensitive data from log messages.

    Args:
        message: Original message

    Returns:
        Message with sensitive data redacted
    """
    filtered = message
    for pattern in SENSITIVE_PATTERNS:
        filtered = re.sub(pattern, "[REDACTED]", filtered, flags=re.IGNORECASE)
    return filtered


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs log records as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON.

        Args:
            record: Log record to format

        Returns:
            JSON string representation of the log record
        """
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "module": record.module,
            "message": _filter_sensitive_data(record.getMessage()),
        }

        # Add extra fields if present
        if hasattr(record, "duration_ms"):
            log_entry["duration_ms"] = record.duration_ms
        if hasattr(record, "extra"):
            for key, value in record.extra.items():
                log_entry[key] = _filter_sensitive_data(str(value))

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


class DurationLogger:
    """Context manager to log duration of operations."""

    def __init__(self, logger: logging.Logger, message: str, level: int = logging.INFO):
        """Initialize duration logger.

        Args:
            logger: Logger instance to use
            message: Log message to prefix with duration
            level: Log level to use (default: INFO)
        """
        self.logger = logger
        self.message = message
        self.level = level
        self.start_time = None

    def __enter__(self):
        """Start timing."""
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """End timing and log duration."""
        duration_ms = (time.time() - self.start_time) * 1000
        extra = {"duration_ms": round(duration_ms, 2)}
        if exc_type is not None:
            self.logger.log(
                self.level,
                f"{self.message} - FAILED ({duration_ms:.2f}ms)",
                extra=extra,
                exc_info=True,
            )
        else:
            self.logger.log(
                self.level,
                f"{self.message} - completed ({duration_ms:.2f}ms)",
                extra=extra,
            )


def setup_logging(
    log_level: int = logging.INFO,
    log_dir: Optional[Path] = None,
    log_file: str = "lifequery.log",
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 3,
) -> logging.Logger:
    """Setup structured logging for LifeQuery.

    Args:
        log_level: Logging level (default: INFO)
        log_dir: Directory for log files (default: DATA_DIR/logs)
        log_file: Name of log file (default: lifequery.log)
        max_bytes: Maximum size of log file before rotation (default: 10MB)
        backup_count: Number of backup files to keep (default: 3)

    Returns:
        Configured logger instance
    """
    if log_dir is None:
        log_dir = DATA_DIR / "logs"

    # Create log directory if it doesn't exist
    log_dir.mkdir(parents=True, exist_ok=True)

    log_path = log_dir / log_file

    # Create root logger
    logger = logging.getLogger("lifequery")
    logger.setLevel(log_level)

    # Remove existing handlers to avoid duplicates
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # File handler with rotation
    file_handler = RotatingFileHandler(
        filename=log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(JSONFormatter())

    # Console handler for development
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info(
        f"Logging configured: {log_path}, rotation at {max_bytes} bytes, "
        f"keeping {backup_count} backups"
    )

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance.

    Args:
        name: Name for the logger (typically __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(f"lifequery.{name}")
