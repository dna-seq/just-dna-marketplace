"""
Logging configuration. `MARKETPLACE_DEBUG=true` turns everything up to DEBUG on stdout so a live
server's console/journal shows exactly what happened — request tracing, the compiler/duckdb/uvicorn
internals, and the Eliot-structured publish/import steps.

Eliot messages (the app's structured action logs) are bridged into stdlib logging at DEBUG level,
so they surface only when debug is on and share one stdout stream with everything else.
"""

import json
import logging
import sys

from eliot import add_destinations

from just_dna_marketplace.config import Settings

# Third-party loggers that are too chatty at DEBUG — pinned to WARNING.
_NOISY: tuple[str, ...] = (
    "polars", "duckdb", "httpx", "httpcore", "multipart", "python_multipart",
    "huggingface_hub", "urllib3", "asyncio",
)

_eliot_wired = False


def _eliot_to_stdlib(message: dict) -> None:
    """Forward each Eliot message to the `eliot` stdlib logger as a JSON line (DEBUG)."""
    logging.getLogger("eliot").debug(json.dumps(message, default=str))


def configure_logging(settings: Settings) -> None:
    """Configure stdout logging from settings. Safe to call repeatedly (tests re-create the app)."""
    level = (
        logging.DEBUG
        if settings.debug
        else getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        force=True,
    )
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    global _eliot_wired
    if not _eliot_wired:
        add_destinations(_eliot_to_stdlib)
        _eliot_wired = True

    logging.getLogger("marketplace").debug("logging configured (debug=%s)", settings.debug)
