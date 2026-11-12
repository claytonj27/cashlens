#!/usr/bin/env python3
"""The acceptance journey of design section 5A.1, run against the real API and database.

Five turns on a synthetic demo business built for this check:

  1. "What changed this month?"
  2. "Which transactions explain the supplier change?"
  3. "What if those receipts arrive three days later?"  -> must CLARIFY, not compute
  4. the owner picks one of the offered options          -> computes, chart, explanation
  5. "And if it's a week instead?"                       -> same figures, new delay

What is being checked is not that the model writes well. It is that step 3 refuses to turn an
all-receipts request into a single late payment, that step 4 only computes once the owner has
chosen, and that step 5 carries the agreed amount and date forward while changing only the
delay. Each turn's tools, validation, cost and latency are printed as they happened.

    API_BASE=http://127.0.0.1:8000 services/api/.venv/bin/python db/checks/ask_journey.py --live

Settled spend records are never deleted: they are the milestone's ledger.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "services" / "api" / ".env"
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_BYPASS = os.environ.get("API_BYPASS", "").strip()
CLIENT = {"x-cashlens-client": "web"}
SLUG = "comparison-bench"
STATE = ROOT / "local-data" / "ask-journey.json"
# This task's own ceiling, on top of the milestone cap the database enforces. Micro-dollars.
TASK_BUDGET_MICRO = 3_000_000


def env(key: str) -> str:
    match = re.search(rf"^{key}=(.*)$", ENV_FILE.read_text(encoding="utf-8"), re.MULTILINE)
    if not match or not match.group(1).strip():
        sys.exit(f"{key} is not set in services/api/.env")
    return match.group(1).strip()


def psql(sql: str) -> str:
    result = subprocess.run(
        ["psql", env("SUPABASE_DB_URL"), "-X", "-v", "ON_ERROR_STOP=1", "-q", "-tA"],
        input=sql,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.exit(f"psql failed:\n{result.stderr.strip()[:1200]}")
    return result.stdout.strip()


def http(url: str, method: str = "GET", body: Any = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": "cashlens-journey", **(headers or {})}
    if API_BYPASS and url.startswith(API_BASE):
        h["x-vercel-protection-bypass"] = API_BYPASS
    if body is not None:
        h["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {}), response.headers.get(
                "set-cookie", ""
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw), ""
        except ValueError:
            return exc.code, {"raw": raw[:300].decode(errors="replace")}, ""


def ledger() -> int:
    return int(psql("select settled_micro from public.ai_milestone_spend();"))


def money(micro: int | None) -> str:
    return "—" if micro is None else f"${micro / 1_000_000:.6f}"


def show(label: str, body: dict[str, Any]) -> None:
    run = body.get("run") or {}
    print(f"\n{'=' * 78}\n{label}")
    print(f"  status   {body.get('status')}   rounds {run.get('attempts')}   "
          f"{run.get('latency_ms')} ms   {money(run.get('cost_micro'))}")
    for call in body.get("tool_calls") or []:
        mark = "OK " if call["ok"] else "REFUSED"
        print(f"  tool     [{mark}] {call['tool']}({json.dumps(call['arguments'])})")
        if not call["ok"]:
            print(f"             -> {call['summary']}")
    validation = run.get("validation") or {}
    if validation.get("schema"):
        print(
            f"  layers   schema {'ok' if validation['schema']['ok'] else 'FAILED'}"
            f" · evidence {'ok' if validation['evidence']['ok'] else 'FAILED'}"
            f" · numbers {validation.get('claims_withheld', 0) or 'ok'}"
            f" · support {'ok' if validation['support']['ok'] else 'FAILED'}"
        )
    answer = body.get("answer") or {}
    if answer:
        print(f"  ANSWER   {answer.get('answer')}")
        for claim in answer.get("claims", []):
            print(f"     - ({claim['signal_id']}) {claim['text']}")
        if answer.get("sources"):
            print(f"  sources  {', '.join(answer['sources'])}")
        for reason in answer.get("withheld_reasons", []):
            print(f"  withheld {reason}")
    if body.get("clarify"):
        print(f"  CLARIFY  {body['clarify']['question']}")
        for option in body["clarify"]["options"]:
            print(f"     [ ] {option['label']}  -> {json.dumps(option['scenario'])}")
    if body.get("scenario_applied"):
        print(f"  scenario {json.dumps(body['scenario_applied'])}")
    if body.get("message"):
        print(f"  message  {body['message']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print("This makes paid calls. Re-run with --live.")
        return 0

    start = ledger()
    print(f"milestone spend before: {money(start)}")

    status, _, cookie = http(f"{API_BASE}/api/demo/session", "POST", None, CLIENT)
    visitor = re.search(r"cl_visitor=([^;]+)", cookie)
    if not visitor:
        sys.exit(f"no demo session: {status}")
    H = {**CLIENT, "Cookie": f"cl_visitor={visitor.group(1)}"}
    status, workspace, _ = http(f"{API_BASE}/api/demo/workspaces/{SLUG}", "POST", None, H)
    if status != 200:
        sys.exit(f"could not open the bench workspace: {status} {workspace}")
    wid = workspace["workspace_id"]
    base = f"{API_BASE}/api/workspaces/{wid}/ai"
    print(f"workspace {wid}")

    status, offered, _ = http(f"{base}/ask/suggestions", headers=H)
    print(f"\nsuggested questions ({status}): " + " | ".join(i["text"] for i in offered["items"]))

    turns: list[dict[str, Any]] = []
    conversation: str | None = None

    def turn(label: str, text: str, option_id: str | None = None) -> dict[str, Any]:
        nonlocal conversation
        spent = ledger() - start
        if spent >= TASK_BUDGET_MICRO:
            print(f"\n!! task ceiling reached ({money(spent)}); stopping")
            return {}
        body = {"text": text, "conversation_id": conversation, "option_id": option_id}
        code, answer, _ = http(f"{base}/ask", "POST", body, H)
        if code != 200:
            print(f"\n{label}: HTTP {code} {json.dumps(answer)[:300]}")
            return {}
        conversation = answer.get("conversation_id") or conversation
        show(f"{label}\n  asked:   {text}", answer)
        turns.append({"label": label, "question": text, **answer})
        return answer

    turn("1. what changed", "What changed this month?")
    turn("2. which transactions", "Which transactions explain the supplier change?")
    third = turn("3. ambiguous what-if", "What if those receipts arrive three days later?")

    option_id = None
    for option in (third.get("clarify") or {}).get("options", []):
        option_id = option["id"]
        break
    if option_id:
        turn("4. owner picks one", "That one, please.", option_id=option_id)
    else:
        print("\n!! step 3 did not offer a choice; step 4 is skipped")
    turn("5. follow-up", "And if it's a week instead?")

    end = ledger()
    print(f"\n{'=' * 78}")
    print(f"milestone spend after: {money(end)}   this run: {money(end - start)}")
    rows = psql(
        "select count(*) from public.ai_turns t join public.ai_conversations c "
        f"on c.id = t.conversation_id where c.workspace_id = '{wid}';"
    )
    print(f"turns stored: {rows}")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps({"workspace": wid, "spend_micro": end - start, "turns": turns}, indent=2),
        encoding="utf-8",
    )
    print(f"full detail written to {STATE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
