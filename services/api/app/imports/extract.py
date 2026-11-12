"""Running PDF extraction in its own process, with a wall clock the parent actually enforces.

Why a separate interpreter and not a fork: the import request runs on an AnyIO worker thread
while other threads are alive, and the Supabase client is module-cached with an HTTP connection
pool whose lock is shared. A fork copies only the calling thread, so any lock another thread
held at that instant stays locked forever in the child. A fresh process inherits no Python
locks, no threads and no sockets, and takes only the bytes and the limits it is given.

What is bounded, and what is not:

* wall clock, CPU seconds and address space per extraction, plus the decompressed output pypdf
  will produce, are all enforced;
* `RLIMIT_AS` bounds address space, which is close to but not the same as resident memory;
* these are per child. They do not by themselves bound what one function instance uses in
  aggregate, so `MAX_CONCURRENT` caps how many extractions run at once **in this instance**.
  Nothing coordinates across instances, and the platform's own memory ceiling still applies to
  the whole function.
"""

import json
import logging
import os
import subprocess
import sys
import threading
from typing import Any

from app.config import get_settings
from app.imports.csvparse import Table, UnsupportedFileError

logger = logging.getLogger(__name__)

WORKER_MODULE = "app.imports.extract_worker"
EXIT_REFUSED = 3
EXIT_TOO_LARGE = 4

# Refusals that are about this run, not about the file. The upload stays in Storage and the
# import stays retryable; treating them as "unsupported file" would delete the owner's upload
# for a transient condition.
RETRYABLE_CODES = frozenset(
    {"extraction_timeout", "extraction_busy", "extraction_unavailable", "extraction_exhausted"}
)

# Bounds concurrent extraction inside ONE function instance, so several large files cannot
# add up against the platform's memory ceiling. It is process-local on purpose and says
# nothing about other instances.
_slots: threading.Semaphore | None = None
_slots_guard = threading.Lock()


def _semaphore(limit: int) -> threading.Semaphore:
    global _slots
    with _slots_guard:
        if _slots is None:
            _slots = threading.Semaphore(limit)
        return _slots


def _worker_environment(settings: Any) -> dict[str, str]:
    """Only what the worker needs to parse. No credentials, no connection settings."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    existing = os.environ.get("PYTHONPATH", "")
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": f"{root}{os.pathsep}{existing}" if existing else root,
        "PYTHONUNBUFFERED": "1",
        "HOME": os.environ.get("HOME", "/tmp"),
        "CASHLENS_EXTRACT_ADDRESS_SPACE_BYTES": str(settings.extract_address_space_bytes),
        "CASHLENS_EXTRACT_CPU_SOFT_SECONDS": str(settings.extract_cpu_soft_seconds),
        "CASHLENS_EXTRACT_CPU_HARD_SECONDS": str(settings.extract_cpu_hard_seconds),
        "CASHLENS_EXTRACT_ZLIB_MAX_OUTPUT_BYTES": str(settings.extract_zlib_max_output_bytes),
        "CASHLENS_EXTRACT_MAX_RESULT_BYTES": str(settings.extract_max_result_bytes),
    }


def extract_pdf(data: bytes, timeout_seconds: float | None = None) -> Table:
    """Parse a PDF in a child process. Raises UnsupportedFileError for anything refusable."""
    settings = get_settings()
    timeout = timeout_seconds or settings.extract_wall_clock_seconds
    slots = _semaphore(settings.extract_max_concurrent)
    if not slots.acquire(timeout=settings.extract_queue_seconds):
        raise UnsupportedFileError(
            "extraction_busy",
            "The importer is reading other files right now; try again in a moment.",
        )
    try:
        return _run(data, timeout, settings)
    finally:
        slots.release()


def _run(data: bytes, timeout: float, settings: Any) -> Table:
    command = [sys.executable, "-m", WORKER_MODULE]
    try:
        # communicate() underneath drains stdout and stderr while the input is written, so a
        # large result cannot wedge the pipe. On timeout it kills the child and reaps it.
        done = subprocess.run(  # noqa: S603 - fixed command, no shell, no caller input in argv
            command,
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # never read, and capturing it is unbounded
            timeout=timeout,
            env=_worker_environment(settings),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning("extract.timeout", extra={"timeout_seconds": timeout})
        raise UnsupportedFileError(
            "extraction_timeout",
            "Reading this PDF took longer than the importer allows; nothing was changed.",
        ) from exc
    except (OSError, ValueError) as exc:
        logger.error("extract.spawn_failed", extra={"error_class": type(exc).__name__})
        raise UnsupportedFileError(
            "extraction_unavailable", "The importer could not read this file; try again."
        ) from exc

    if len(done.stdout) > settings.extract_max_result_bytes:
        raise UnsupportedFileError(
            "extraction_result_too_large",
            "This PDF produced more extracted data than the importer accepts.",
        )
    payload = _decode(done)
    refused = payload.get("refused")
    if refused:
        raise UnsupportedFileError(refused["code"], refused["message"])
    table = payload.get("table")
    if done.returncode != 0 or not isinstance(table, dict):
        _raise_for_exit(done)
    missing = {k: v for k, v in (payload.get("limits") or {}).items() if v != "applied"}
    if missing:
        # A platform that refuses a limit weakens the guarantee; say so rather than assume it
        # held. The parent's wall clock still bounded this run.
        logger.warning("extract.limit_unavailable", extra={"limits": missing})
    return _table_from(table)


def _decode(done: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    if not done.stdout:
        return {}
    try:
        loaded = json.loads(done.stdout)
    except (ValueError, UnicodeDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _raise_for_exit(done: subprocess.CompletedProcess[bytes]) -> None:
    code = done.returncode
    if code == EXIT_TOO_LARGE:
        raise UnsupportedFileError(
            "extraction_result_too_large",
            "This PDF produced more extracted data than the importer accepts.",
        )
    if code < 0:
        # Killed by a signal: SIGXCPU or SIGKILL from RLIMIT_CPU, or the kernel's OOM path.
        logger.warning("extract.killed", extra={"signal": -code})
        raise UnsupportedFileError(
            "extraction_exhausted",
            "Reading this PDF needed more time or memory than the importer allows.",
        )
    logger.error("extract.failed", extra={"exit_code": code})
    raise UnsupportedFileError(
        "extraction_unavailable", "The importer could not read this file; try again."
    )


def _table_from(payload: dict[str, Any]) -> Table:
    """Rebuild the table, refusing anything that is not the shape the parser promises."""
    try:
        header = [str(h) for h in payload["header"]]
        rows = [[str(cell) for cell in row] for row in payload["rows"]]
        line_numbers = [int(n) for n in payload["line_numbers"]]
        locators = payload.get("locators")
        pages = payload.get("pages")
        statement = payload.get("statement")
        table = Table(
            header=header,
            rows=rows,
            delimiter=str(payload["delimiter"]),
            encoding=str(payload["encoding"]),
            byte_size=int(payload["byte_size"]),
            line_numbers=line_numbers,
            source=str(payload.get("source") or "pdf"),
            locators=[str(x) for x in locators] if locators else None,
            pages=[int(x) for x in pages] if pages else None,
            statement=statement if isinstance(statement, dict) else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UnsupportedFileError(
            "extraction_unavailable", "The importer could not read this file; try again."
        ) from exc
    if len(rows) != len(line_numbers) or any(len(r) != len(header) for r in rows):
        raise UnsupportedFileError(
            "extraction_unavailable", "The importer could not read this file; try again."
        )
    return table
