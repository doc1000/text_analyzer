"""
service_errors.py

Centralized error classification and response protocol module.

Purpose
-------
Provide a consistent and extensible framework for:

- Classifying service errors (external + internal)
- Determining retry behavior
- Returning structured error responses
- Emitting structured logs for observability
- Decoupling user-facing messaging from backend logic

Design Principles
-----------------
1. Errors are classified into categories (warmup, transient, permanent, internal).
2. Each classified error includes retry metadata.
3. Backend returns structured error keys, not raw messages.
4. Frontend resolves error keys via a versioned error map.
5. Structured logs enable trend monitoring (Fly logs, ELK, etc).

Usage Flow
----------
try:
    result = call_external_service(...)
except Exception as exc:
    classified = classify_error(exc, service="huggingface")
    if classified.retryable:
        retry_logic(...)
    else:
        raise HTTPException(status_code=classified.http_status,
                            detail=classified.to_dict())

Frontend receives:
{
    "error_key": "HF_WARMING",
    "category": "warmup",
    "retryable": true
}

Frontend maps error_key to user message via static error_map.json.

Extending
---------
Add new classification rules in classify_error().
Do NOT hardcode user-facing messages here.
"""

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional, Callable
import logging
import time


# ---------------------------------------------------------------------
# Error Categories
# ---------------------------------------------------------------------

class ErrorCategory(str, Enum):
    WARMUP = "warmup"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    INTERNAL = "internal"


# ---------------------------------------------------------------------
# Classified Error Object
# ---------------------------------------------------------------------

@dataclass
class ClassifiedError:
    service: str
    category: ErrorCategory
    error_key: str
    retryable: bool
    http_status: int
    original_exception: Optional[Exception] = None

    def to_dict(self):
        """Return safe, frontend-ready representation."""
        return {
            "error_key": self.error_key,
            "category": self.category.value,
            "retryable": self.retryable,
        }


# ---------------------------------------------------------------------
# Classification Logic
# ---------------------------------------------------------------------

def classify_error(exc: Exception, service: str) -> ClassifiedError:
    """
    Classify an exception into a structured error type.

    Parameters
    ----------
    exc : Exception
        The raised exception.
    service : str
        Name of the upstream service ("huggingface", "internal", etc).

    Returns
    -------
    ClassifiedError
    """

    msg = str(exc).lower()

    # Hugging Face warmup detection
    if service == "huggingface":
        if "loading" in msg or "model is loading" in msg:
            return ClassifiedError(
                service=service,
                category=ErrorCategory.WARMUP,
                error_key="HF_WARMING",
                retryable=True,
                http_status=503,
                original_exception=exc,
            )

        if "timeout" in msg or "connection" in msg:
            return ClassifiedError(
                service=service,
                category=ErrorCategory.TRANSIENT,
                error_key="HF_TIMEOUT",
                retryable=True,
                http_status=504,
                original_exception=exc,
            )

    # Generic timeouts
    if "timeout" in msg:
        return ClassifiedError(
            service=service,
            category=ErrorCategory.TRANSIENT,
            error_key="TRANSIENT_TIMEOUT",
            retryable=True,
            http_status=504,
            original_exception=exc,
        )

    # Internal service cold start detection
    if "health check" in msg or "connection refused" in msg:
        return ClassifiedError(
            service=service,
            category=ErrorCategory.WARMUP,
            error_key="INTERNAL_COLD",
            retryable=True,
            http_status=503,
            original_exception=exc,
        )

    # Fallback
    return ClassifiedError(
        service=service,
        category=ErrorCategory.INTERNAL,
        error_key="UNEXPECTED_ERROR",
        retryable=False,
        http_status=500,
        original_exception=exc,
    )


# ---------------------------------------------------------------------
# Retry Wrapper
# ---------------------------------------------------------------------

def retry_call(
    func: Callable,
    retries: int = 2,
    backoff_seconds: float = 1.0,
    service: str = "unknown"
):
    """
    Retry wrapper for transient/warmup errors.

    Parameters
    ----------
    func : Callable
        Function to execute.
    retries : int
        Number of retries.
    backoff_seconds : float
        Base backoff.
    service : str
        Service name for classification.

    Returns
    -------
    Function result or raises ClassifiedError
    """

    attempt = 0
    while attempt <= retries:
        try:
            return func()
        except Exception as exc:
            classified = classify_error(exc, service=service)

            log_error(classified, attempt)

            if not classified.retryable or attempt == retries:
                raise classified

            sleep_time = backoff_seconds * (2 ** attempt)
            time.sleep(sleep_time)
            attempt += 1


# ---------------------------------------------------------------------
# Structured Logging
# ---------------------------------------------------------------------

logger = logging.getLogger("service_errors")


def log_error(classified: ClassifiedError, attempt: int = 0):
    """
    Emit structured log for observability.

    This enables:
    - Drift tracking
    - Warmup frequency monitoring
    - External provider instability tracking
    """

    logger.error(
        "service_error",
        extra={
            "service": classified.service,
            "category": classified.category.value,
            "error_key": classified.error_key,
            "retryable": classified.retryable,
            "attempt": attempt,
        },
    )
