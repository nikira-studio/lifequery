"""Telegram authentication router."""

import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from schemas import (
    ConnectedResponse,
    NeedsAuthResponse,
    PhoneRequest,
    PhoneSentErrorResponse,
    PhoneSentResponse,
    TelegramStatusResponse,
    VerifyRequest,
)
from telegram.telethon_sync import (
    auto_sync_chats,
    disconnect_telegram,
    get_telegram_status,
    start_auth,
    verify_auth,
)
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

# Rate limiting for auth/verify endpoint
# Track attempts by phone number: {phone: [(timestamp, timestamp, ...)]}
_rate_limit_attempts: dict[str, list[float]] = {}

RATE_LIMIT_WINDOW = 600  # 10 minutes in seconds
MAX_ATTEMPTS = 5


def _check_rate_limit(phone: str) -> tuple[bool, int]:
    """Check if the phone number has exceeded the rate limit.

    Args:
        phone: Phone number to check

    Returns:
        Tuple of (allowed, remaining_attempts)
    """
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW

    # Get or initialize attempts list for this phone
    attempts = _rate_limit_attempts.get(phone, [])

    # Filter out old attempts outside the time window
    recent_attempts = [t for t in attempts if t > cutoff]

    # Check if limit exceeded
    remaining_attempts = MAX_ATTEMPTS - len(recent_attempts)
    allowed = remaining_attempts > 0

    return allowed, remaining_attempts


def _record_attempt(phone: str) -> None:
    """Record a verification attempt for the phone number.

    Args:
        phone: Phone number to record attempt for
    """
    now = time.time()
    if phone not in _rate_limit_attempts:
        _rate_limit_attempts[phone] = []

    _rate_limit_attempts[phone].append(now)

    # Clean up old entries periodically
    cutoff = now - RATE_LIMIT_WINDOW
    _rate_limit_attempts[phone] = [t for t in _rate_limit_attempts[phone] if t > cutoff]


@router.get("/status", response_model=TelegramStatusResponse)
async def telegram_status() -> TelegramStatusResponse:
    """Get current Telegram connection status.

    Returns one of these states:
    - uninitialized: Telegram API credentials not configured
    - needs_auth: Credentials present but no session file
    - phone_sent: Code request in flight, waiting for verification
    - connected: Session exists and active
    """
    try:
        result: dict[str, Any] = await get_telegram_status()
        return TelegramStatusResponse(**result)
    except Exception as e:
        logger.error(f"Error getting telegram status: {e}", exc_info=True)
        return TelegramStatusResponse(state="error", detail=str(e))


@router.post("/auth/start", response_model=PhoneSentResponse)
async def auth_start(request: PhoneRequest) -> PhoneSentResponse:
    """Start Telegram authentication - send code to phone.

    The system will send a verification code via SMS or Telegram app.
    Use /auth/verify with the code to complete authentication.

    Note: For 2FA-enabled accounts, you'll receive a token that must be used
    with /auth/verify along with the code.
    """
    try:
        result = await start_auth(request.phone)

        # Check if 2FA is required (token is returned in that case)
        if "token" in result:
            logger.info(f"Auth started for {request.phone} - 2FA required")
            # Token is included in result, client should handle it
            return PhoneSentResponse(**result)

        logger.info(f"Auth started for {request.phone}")
        return PhoneSentResponse(**result)

    except ValueError as e:
        logger.warning(f"Invalid auth start request: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting auth: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to start authentication: {str(e)}"
        )


@router.post("/auth/verify")
async def auth_verify(request: VerifyRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Verify the authentication code and complete login.

    For normal authentication: send phone and code
    For 2FA accounts: send phone, code, and token (returned from /auth/start)

    Success: returns { "state": "connected" }
    Wrong code: returns { "state": "phone_sent", "error": "..." }

    Rate limited: 5 attempts per 10 minutes per phone number
    """
    # If phone is provided, normalize it immediately for consistent lookup/rate limiting
    if request.phone:
        from telegram.telethon_sync import normalize_phone
        request.phone = normalize_phone(request.phone)

    # Get phone number for rate limiting (use provided phone or derive from token)
    phone_for_rate_limit = request.phone
    if not phone_for_rate_limit and request.token:
        # Derive phone from token for rate limiting
        from telegram.telethon_sync import _auth_tokens

        phone_for_rate_limit = _auth_tokens.get(request.token, "")

    if not phone_for_rate_limit:
        raise HTTPException(
            status_code=400, detail="Phone number or token required for verification"
        )

    # Check rate limit
    allowed, remaining = _check_rate_limit(phone_for_rate_limit)
    if not allowed:
        logger.warning(f"Rate limit exceeded for phone {phone_for_rate_limit}")
        raise HTTPException(
            status_code=429,
            detail=f"Too many verification attempts. Please wait 10 minutes before trying again.",
        )

    # Record this attempt
    _record_attempt(phone_for_rate_limit)

    try:
        # If token is provided, use token-based verification (2FA or phone_sent)
        if request.token:
            result = await verify_auth(request.token, request.code, request.password)
        else:
            # For phone-based verification
            # Note: This requires telethon_sync.py to support phone+code verification
            # For now, we require token for security
            if not request.phone:
                raise ValueError(
                    "Either phone or token must be provided for verification"
                )
            # Try to find the token associated with this phone
            from telegram.telethon_sync import _auth_tokens

            token = None
            for t, p in _auth_tokens.items():
                if p == request.phone:
                    token = t
                    break

            if not token:
                raise ValueError(
                    "No active auth session found for this phone. "
                    "Possible reasons: session expired, backend restarted, or phone number mismatch."
                )

            result = await verify_auth(token, request.code, request.password)

        # Handle verification errors
        if "error" in result:
            logger.warning(f"Verification failed: {result.get('error')}")
            # Return error in the format expected by BUILD_PLAN
            if result.get("state") == "phone_sent":
                return PhoneSentErrorResponse(
                    state="phone_sent",
                    error=result.get("error", "The code you entered is invalid."),
                ).model_dump()
            # Include token in result if this was a 2FA request
            if request.token:
                result["token"] = request.token
            return result

        # Successful verification — discover chats in the background so the
        # Data tab is populated immediately without a manual sync step.
        logger.info("Telegram authentication successful")
        background_tasks.add_task(auto_sync_chats)
        return ConnectedResponse(**result).model_dump()

    except ValueError as e:
        if "Two-step" in str(e) or "2FA" in str(e):
            raise HTTPException(
                status_code=400,
                detail="Two-step verification required. "
                "Your account has two-factor authentication enabled. "
                "Please use the token from /auth/start with the verification code.",
            )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error verifying code: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Verification failed")


@router.post("/disconnect", response_model=NeedsAuthResponse)
async def telegram_disconnect() -> NeedsAuthResponse:
    """Disconnect and clear the Telegram session.

    This deletes the session file and resets the authentication state.
    You'll need to re-authenticate to sync messages again.
    """
    try:
        result = await disconnect_telegram()
        logger.info("Telegram disconnected successfully")
        return NeedsAuthResponse(**result)
    except Exception as e:
        logger.error(f"Error disconnecting: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to disconnect")
