"""The PDF extraction worker. Runs as its own process; never touches a database or a network.

Started by `app.imports.extract` as `sys.executable -m app.imports.extract_worker`, it reads
the file from standard input, applies its resource limits **before** any expensive parsing,
and writes one JSON object to standard output. It publishes nothing itself: the parent stages
the result through the existing lease-guarded function.

A separate interpreter rather than a fork: the request runs on an AnyIO worker thread while
other threads hold locks that a fork would copy in their locked state, among them the module
cached Supabase client's connection pool lock. A fresh process inherits no Python locks, no
threads and no sockets.
"""

import json
import os
import resource
import sys
from typing import Any

EXIT_OK = 0
EXIT_REFUSED = 3  # the file was read and rejected for a stated reason
EXIT_TOO_LARGE = 4  # the result exceeded what the parent accepts
EXIT_INTERNAL = 5


def _apply_limits() -> dict[str, str]:
    """Address space and CPU, set before the file is parsed. Each one applies on its own.

    A platform that refuses a limit must not make the file unreadable, so each is attempted
    separately and the outcome is reported back. Darwin refuses RLIMIT_AS outright, which is
    why local runs report it as unavailable while Linux applies it. The parent's wall clock is
    enforced whatever happens here, so there is always a bound.

    RLIMIT_AS bounds address space, which is close to but not the same as resident memory.
    The CPU soft limit sits below the hard one so SIGXCPU can surface as a clean refusal and
    SIGKILL remains the backstop.
    """
    applied: dict[str, str] = {}
    address_space = int(os.environ.get("CASHLENS_EXTRACT_ADDRESS_SPACE_BYTES", 0))
    cpu_soft = int(os.environ.get("CASHLENS_EXTRACT_CPU_SOFT_SECONDS", 0))
    cpu_hard = int(os.environ.get("CASHLENS_EXTRACT_CPU_HARD_SECONDS", 0))
    if address_space > 0:
        try:
            resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))
            applied["address_space"] = "applied"
        except (ValueError, OSError) as exc:
            applied["address_space"] = f"unavailable:{type(exc).__name__}"
    if cpu_soft > 0 and cpu_hard >= cpu_soft:
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_soft, cpu_hard))
            applied["cpu"] = "applied"
        except (ValueError, OSError) as exc:
            applied["cpu"] = f"unavailable:{type(exc).__name__}"
    return applied


def _table_payload(table: Any) -> dict[str, Any]:
    return {
        "header": table.header,
        "rows": table.rows,
        "delimiter": table.delimiter,
        "encoding": table.encoding,
        "byte_size": table.byte_size,
        "line_numbers": table.line_numbers,
        "source": table.source,
        "locators": table.locators,
        "pages": table.pages,
        "statement": table.statement,
    }


def main() -> int:
    data = sys.stdin.buffer.read()
    limits = _apply_limits()

    # Imported after the limits are in place so even loading the parser is covered by them.
    from pypdf._configuration import apply_configuration

    from app.imports import pdfparse
    from app.imports.csvparse import UnsupportedFileError

    zlib_limit = int(os.environ.get("CASHLENS_EXTRACT_ZLIB_MAX_OUTPUT_BYTES", 0))
    max_result = int(os.environ.get("CASHLENS_EXTRACT_MAX_RESULT_BYTES", 0))
    try:
        # pypdf bounds this while decompressing, by passing it to zlib as max_length, so a
        # compression bomb never allocates past it. A size check afterwards could not.
        with apply_configuration(zlib_maximum_output_length=zlib_limit):
            table = pdfparse.build_table(data)
    except UnsupportedFileError as exc:
        sys.stdout.write(json.dumps({"refused": {"code": exc.code, "message": str(exc)}}))
        return EXIT_REFUSED
    except MemoryError:
        sys.stdout.write(
            json.dumps(
                {
                    "refused": {
                        "code": "extraction_out_of_memory",
                        "message": "Reading this PDF needed more memory than the importer allows.",
                    }
                }
            )
        )
        return EXIT_REFUSED
    except Exception as exc:  # noqa: BLE001 - any parser failure is the owner's refusal
        # pypdf raises its own limit errors; they are refusals, not crashes.
        name = type(exc).__name__
        if name == "LimitReachedError":
            sys.stdout.write(
                json.dumps(
                    {
                        "refused": {
                            "code": "pdf_content_too_large",
                            "message": "A page of this PDF expands to more data "
                            "than the importer reads.",
                        }
                    }
                )
            )
            return EXIT_REFUSED
        sys.stdout.write(json.dumps({"error": "parse", "detail": name}))
        return EXIT_INTERNAL

    payload = json.dumps({"table": _table_payload(table), "limits": limits})
    if max_result > 0 and len(payload) > max_result:
        sys.stdout.write(
            json.dumps(
                {
                    "refused": {
                        "code": "extraction_result_too_large",
                        "message": "This PDF produced more extracted data "
                        "than the importer accepts.",
                    }
                }
            )
        )
        return EXIT_TOO_LARGE
    sys.stdout.write(payload)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
