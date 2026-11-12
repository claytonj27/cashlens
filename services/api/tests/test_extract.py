"""Isolated extraction: real child processes, real limits, controlled errors.

These run the actual worker, so they prove the boundary rather than a stand-in of it. The
workloads are small and synthetic; nothing here reads an untrusted corpus.
"""

import json
import subprocess
import sys
import time
import zlib
from pathlib import Path

import pytest

from app.config import get_settings
from app.imports import extract
from app.imports.csvparse import UnsupportedFileError

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "statements"
WORKER = [sys.executable, "-m", "app.imports.extract_worker"]


def build_pdf(ops: bytes, compress: bool = True) -> bytes:
    """A one-page PDF whose content stream is `ops`."""
    stream = zlib.compress(ops, 9) if compress else ops
    filt = b"/Filter /FlateDecode " if compress else b""
    objs = [
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" "
        + filt
        + b">>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Page /Parent 4 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 1 0 R >> >> /Contents 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Catalog /Pages 4 0 R >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(len(objs) + 1).encode()
        + b" /Root 5 0 R >>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


def heavy_page(operators: int) -> bytes:
    ops = (
        b"BT /F1 9 Tf\n"
        + b"".join(b"1 0 0 1 %d %d Tm (x) Tj\n" % (i % 600, i % 780) for i in range(operators))
        + b"ET\n"
    )
    return build_pdf(ops)


def worker_env(**overrides: object) -> dict[str, str]:
    settings = get_settings()
    env = extract._worker_environment(settings)
    env.update({k: str(v) for k, v in overrides.items()})
    return env


def test_a_supported_pdf_still_parses_through_the_child() -> None:
    table = extract.extract_pdf((FIXTURES / "layout-a-running-balance.pdf").read_bytes())
    assert table.source == "pdf" and len(table.rows) == 24
    assert table.header == ["date", "description", "amount", "balance"]
    assert table.pages and max(table.pages) == 2 and len(table.locators or []) == 24


def test_an_unsupported_pdf_is_a_controlled_refusal_not_a_crash() -> None:
    with pytest.raises(UnsupportedFileError) as excinfo:
        extract.extract_pdf((FIXTURES / "encrypted.pdf").read_bytes())
    assert excinfo.value.code == "pdf_encrypted"


def test_a_stalled_worker_is_terminated_and_reaped() -> None:
    """The parent's wall clock is the bound that always applies, whatever a platform allows."""
    data = heavy_page(400_000)
    started = time.monotonic()
    with pytest.raises(UnsupportedFileError) as excinfo:
        extract.extract_pdf(data, timeout_seconds=1.0)
    elapsed = time.monotonic() - started
    assert excinfo.value.code == "extraction_timeout"
    assert elapsed < 20  # it was stopped, not waited out
    # subprocess.run kills and reaps on timeout, so no child of ours is left behind.
    remaining = subprocess.run(
        ["pgrep", "-f", "app.imports.extract_worker"], capture_output=True, check=False
    )
    assert remaining.returncode != 0 or not remaining.stdout.strip()


def test_a_cpu_limit_stops_the_worker_with_a_signal() -> None:
    done = subprocess.run(
        WORKER,
        input=heavy_page(400_000),
        capture_output=True,
        timeout=120,
        env=worker_env(
            CASHLENS_EXTRACT_CPU_SOFT_SECONDS=1,
            CASHLENS_EXTRACT_CPU_HARD_SECONDS=1,
            CASHLENS_EXTRACT_ADDRESS_SPACE_BYTES=0,
        ),
        check=False,
    )
    assert done.returncode < 0, f"expected a signal, got exit {done.returncode}"


def test_a_decompression_bomb_is_refused_while_decompressing() -> None:
    """pypdf passes the cap to zlib as max_length, so the output is never fully allocated."""
    ops = b"BT /F1 9 Tf\n" + b"(x) Tj\n" + b"% " + b"A" * 4_000_000 + b"\nET\n"
    data = build_pdf(ops)
    assert len(data) < 100_000  # small on disk, large when expanded
    done = subprocess.run(
        WORKER,
        input=data,
        capture_output=True,
        timeout=120,
        env=worker_env(CASHLENS_EXTRACT_ZLIB_MAX_OUTPUT_BYTES=64 * 1024),
        check=False,
    )
    payload = json.loads(done.stdout)
    assert payload["refused"]["code"] in ("pdf_content_too_large", "pdf_malformed")


def test_a_result_beyond_the_cap_is_refused_rather_than_returned() -> None:
    done = subprocess.run(
        WORKER,
        input=(FIXTURES / "layout-a-running-balance.pdf").read_bytes(),
        capture_output=True,
        timeout=120,
        env=worker_env(CASHLENS_EXTRACT_MAX_RESULT_BYTES=512),
        check=False,
    )
    assert done.returncode == 4
    assert json.loads(done.stdout)["refused"]["code"] == "extraction_result_too_large"


def test_a_large_result_does_not_deadlock_the_pipes() -> None:
    """communicate() drains stdout and stderr while the input is written."""
    table = extract.extract_pdf((FIXTURES / "layout-a-injection.pdf").read_bytes())
    assert len(table.rows) == 25


def test_the_worker_reports_which_limits_the_platform_applied() -> None:
    done = subprocess.run(
        WORKER,
        input=(FIXTURES / "layout-c-no-balance.pdf").read_bytes(),
        capture_output=True,
        timeout=120,
        env=worker_env(),
        check=False,
    )
    limits = json.loads(done.stdout)["limits"]
    assert set(limits) == {"address_space", "cpu"}
    # Linux applies both; Darwin refuses RLIMIT_AS, and that is reported rather than assumed.
    assert limits["cpu"] == "applied"
    if sys.platform == "linux":
        assert limits["address_space"] == "applied"


def test_a_malformed_worker_result_is_rejected() -> None:
    class Fake:
        returncode = 0
        stdout = b'{"table": {"header": ["date"], "rows": [["a", "b"]], "delimiter": "pdf", '
        stdout += b'"encoding": "pdf", "byte_size": 1, "line_numbers": [1]}}'
        stderr = b""

    with pytest.raises(UnsupportedFileError) as excinfo:
        extract._table_from(json.loads(Fake.stdout)["table"])
    assert excinfo.value.code == "extraction_unavailable"


def test_the_worker_environment_carries_no_credentials() -> None:
    env = extract._worker_environment(get_settings())
    joined = " ".join(f"{k}={v}" for k, v in env.items()).lower()
    for secret in ("supabase", "service_role", "token", "key", "password", "openai"):
        assert secret not in joined, f"{secret} reached the worker environment"
    assert set(env) <= {
        "PATH",
        "PYTHONPATH",
        "PYTHONUNBUFFERED",
        "HOME",
        "CASHLENS_EXTRACT_ADDRESS_SPACE_BYTES",
        "CASHLENS_EXTRACT_CPU_SOFT_SECONDS",
        "CASHLENS_EXTRACT_CPU_HARD_SECONDS",
        "CASHLENS_EXTRACT_ZLIB_MAX_OUTPUT_BYTES",
        "CASHLENS_EXTRACT_MAX_RESULT_BYTES",
    }


def test_a_retryable_refusal_is_named_so_the_upload_is_not_destroyed() -> None:
    """A timeout is about the run, not the file: routes.py must not delete the owner's upload."""
    assert "extraction_timeout" in extract.RETRYABLE_CODES
    assert "extraction_busy" in extract.RETRYABLE_CODES
    assert "extraction_unavailable" in extract.RETRYABLE_CODES
    assert "extraction_exhausted" in extract.RETRYABLE_CODES
    # A genuine file refusal must NOT be in the set, or a bad PDF would never fail.
    assert "pdf_encrypted" not in extract.RETRYABLE_CODES
    assert "pdf_no_text_layer" not in extract.RETRYABLE_CODES
