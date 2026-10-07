"""Structured logs deliberately exclude exception messages and request bodies."""

import logging

import structlog


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(message)s")
    # HTTP libraries can include URLs/tokens in their debug messages.
    for name in ("aiogram", "aiohttp", "sqlalchemy.engine", "asyncio"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
