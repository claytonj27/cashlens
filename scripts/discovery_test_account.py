#!/usr/bin/env python3
"""A throwaway Discovery test account, created and destroyed by hand.

    DISCOVERY_TEST_PASSWORD='...' services/api/.venv/bin/python scripts/discovery_test_account.py create
    DISCOVERY_TEST_PASSWORD='...' services/api/.venv/bin/python scripts/discovery_test_account.py create --with-business
    services/api/.venv/bin/python scripts/discovery_test_account.py destroy --email discovery-test-abc123@example.org

`create` makes one auth user through the Auth admin API, gives it the `stakeholder` role in
`public.profiles`, and assigns it the phase 2 test template (which must already be loaded with
`python -m app.discovery.seed`). With `--with-business` it also provisions one private business,
so the workspace has something to open. It prints the email address and the user id, and never
the password: the password comes from the environment and is not written anywhere by this
script. `--email` names the address; the default is a random one at example.org.

`destroy` removes everything `create` made for that address: history rows (which no cascade
reaches), the private business, and the auth user, which cascades to the profile, the assignment,
its answers and its visits. Nothing else is listed, modified or deleted.

No real person, email or invite is involved. This is the phase 2 mechanism; invite links replace
it in phase 4.
"""

import argparse
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "services" / "api" / ".env"
TEMPLATE_SLUG = "phase2-discovery-test"
BUSINESS_PREFIX = "Discovery test business"


def env(key: str, required: bool = True) -> str:
    value = os.environ.get(key, "").strip()
    if not value and ENV_FILE.exists():
        match = re.search(rf"^{key}=(.*)$", ENV_FILE.read_text(encoding="utf-8"), re.M)
        value = match.group(1).strip() if match else ""
    if not value and required:
        sys.exit(f"{key} is not set (environment or services/api/.env)")
    return value


def http(url: str, method: str = "GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": "cashlens-discovery-account", **(headers or {})}
    if body is not None:
        h["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            text = response.read()
            return response.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as exc:
        text = exc.read()
        try:
            return exc.code, json.loads(text)
        except Exception:
            return exc.code, text[:200].decode(errors="replace")


class Admin:
    def __init__(self) -> None:
        self.base = env("SUPABASE_URL").rstrip("/")
        key = env("SUPABASE_SERVICE_ROLE_KEY")
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    def rest(self, path: str, method: str = "GET", body=None, prefer: str | None = None):
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        return http(f"{self.base}/rest/v1/{path}", method, body, headers)

    def auth(self, path: str, method: str = "GET", body=None):
        return http(f"{self.base}/auth/v1/{path}", method, body, self.headers)

    def find_user(self, email: str) -> dict | None:
        for page in range(1, 11):
            status, listing = self.auth(f"admin/users?page={page}&per_page=200")
            if status != 200:
                sys.exit(f"could not list users: HTTP {status}")
            users = (listing or {}).get("users", [])
            for user in users:
                if (user.get("email") or "").lower() == email.lower():
                    return user
            if len(users) < 200:
                return None
        return None


def create(args: argparse.Namespace) -> int:
    password = os.environ.get("DISCOVERY_TEST_PASSWORD", "")
    if len(password) < 12:
        sys.exit("DISCOVERY_TEST_PASSWORD must be set in the environment and be at least 12 characters")
    admin = Admin()
    email = args.email or f"discovery-test-{secrets.token_hex(3)}@example.org"
    if admin.find_user(email):
        sys.exit(f"{email} already exists; destroy it first or choose another address")

    slug = args.template or TEMPLATE_SLUG
    status, template = admin.rest(f"discovery_templates?slug=eq.{slug}&select=id,title")
    if status != 200 or not template:
        sys.exit(
            f"template {slug!r} is not loaded; run "
            "`services/api/.venv/bin/python -m app.discovery.seed` first"
        )
    template_id = template[0]["id"]

    status, user = admin.auth(
        "admin/users", "POST", {"email": email, "password": password, "email_confirm": True}
    )
    if status not in (200, 201):
        sys.exit(f"could not create the user: HTTP {status} {user}")
    user_id = user["id"]
    print(f"created auth user {user_id} <{email}>")

    # The profile row exists already: the auth.users trigger wrote it. Give it the role.
    status, profile = admin.rest(
        f"profiles?user_id=eq.{user_id}",
        "PATCH",
        {"role": "stakeholder", "first_name": args.first_name},
        prefer="return=representation",
    )
    if status != 200 or not profile:
        sys.exit(f"could not set the profile role: HTTP {status} {profile}")
    print(f"profile role stakeholder, first name {args.first_name!r}")

    status, assignment = admin.rest(
        "discovery_assignments",
        "POST",
        {"user_id": user_id, "template_id": template_id},
        prefer="return=representation",
    )
    if status != 201 or not assignment:
        sys.exit(f"could not assign the template: HTTP {status} {assignment}")
    print(f"assigned {template[0]['title']!r} (assignment {assignment[0]['id']})")

    if args.with_business:
        status, provisioned = admin.rest(
            "rpc/provision_private_business",
            "POST",
            {"p_user_id": user_id, "p_business_name": f"{BUSINESS_PREFIX} {secrets.token_hex(2)}"},
        )
        if status != 200:
            sys.exit(f"could not provision a business: HTTP {status} {provisioned}")
        row = provisioned[0] if isinstance(provisioned, list) else provisioned
        print(f"provisioned private business {row.get('business_id')} {row.get('business_name')!r}")

    print()
    print("Sign in at /login with that address and the password you supplied. Destroy with:")
    print(f"  services/api/.venv/bin/python scripts/discovery_test_account.py destroy --email {email}")
    return 0


ACCOUNT_TABLES = (
    "profiles",
    "discovery_assignments",
    "discovery_responses",
    "discovery_scenario_responses",
    "discovery_transaction_calibration_responses",
    "discovery_sessions",
    "discovery_response_history",
    "discovery_scenario_response_history",
    "discovery_transaction_calibration_response_history",
)
# The tables no cascade reaches, by design: swept last, after the user's own cascade has written
# its "deleted" records into them.
HISTORY_TABLES = (
    "discovery_response_history",
    "discovery_scenario_response_history",
    "discovery_transaction_calibration_response_history",
)


def remove_account(admin: "Admin", user_id: str) -> None:
    """Delete an auth user and then the history rows the deletion itself creates.

    The order is the point, and it was got wrong once. Deleting the auth user cascades to the
    assignment, its answers and its typed scenario rows, and the `before delete` triggers on
    those write a "deleted" record for every one into the two history tables, which no cascade
    reaches because they carry no foreign key by design. Sweeping history first therefore leaves
    exactly those records behind. So: the user goes first, the sweep goes last.
    """
    status, _ = admin.auth(f"admin/users/{user_id}", "DELETE")
    if status not in (200, 204):
        raise RuntimeError(f"could not delete the auth user: HTTP {status}")
    for table in HISTORY_TABLES:
        admin.rest(f"{table}?user_id=eq.{user_id}", "DELETE")


def leftovers(admin: "Admin", user_id: str) -> list[str]:
    """Which account tables still hold a row for this user, as "table: n" strings."""
    found = []
    for table in ACCOUNT_TABLES:
        status, rows = admin.rest(f"{table}?user_id=eq.{user_id}&select=user_id")
        if rows:
            found.append(f"{table}: {len(rows)}")
    return found


REAL_STAKEHOLDER_DOMAINS = ("stakeholder.example",)
REAL_TEMPLATE_PREFIX = "discovery-"


def destroy(args: argparse.Namespace) -> int:
    admin = Admin()
    user = admin.find_user(args.email)
    if user is None:
        print(f"no auth user with address {args.email}; nothing to do")
        return 0
    user_id = user["id"]
    # A real stakeholder is never a throwaway. No flag overrides this: removing one is a
    # deliberate act done by hand, not by the disposable-account script.
    if args.email.lower().rsplit("@", 1)[-1] in REAL_STAKEHOLDER_DOMAINS:
        sys.exit(f"{args.email} is a real stakeholder address; this script does not remove stakeholders")
    status, tpl = admin.rest("discovery_templates?select=id,slug")
    real_ids = {t["id"] for t in tpl or [] if str(t["slug"]).startswith(REAL_TEMPLATE_PREFIX)}
    status, held = admin.rest(f"discovery_assignments?user_id=eq.{user_id}&select=template_id")
    if any(a["template_id"] in real_ids for a in held or []) and not args.force:
        sys.exit(
            f"{args.email} holds an assignment on a real the customer template; pass --force only "
            "if it really is a throwaway (the reviewer account is not)"
        )
    status, profile = admin.rest(f"profiles?user_id=eq.{user_id}&select=role")
    role = profile[0]["role"] if status == 200 and profile else None
    if role == "admin" and not args.force:
        sys.exit("that account holds the admin role; pass --force if it really is a throwaway")

    # Private businesses this script provisioned, recognised by name and membership.
    status, memberships = admin.rest(f"business_memberships?user_id=eq.{user_id}&select=business_id")
    for membership in memberships or []:
        business_id = membership["business_id"]
        status, rows = admin.rest(
            f"businesses?id=eq.{business_id}&data_mode=eq.private&select=id,name"
        )
        for business in rows or []:
            if business["name"].startswith(BUSINESS_PREFIX) or args.force:
                admin.rest(f"businesses?id=eq.{business['id']}", "DELETE")
                print(f"deleted business {business['id']} {business['name']!r}")
            else:
                print(f"kept business {business['id']} {business['name']!r}: not one this script made")
    try:
        remove_account(admin, user_id)
    except RuntimeError as exc:
        sys.exit(str(exc))
    print(f"deleted auth user {user_id} <{args.email}>")

    left = leftovers(admin, user_id)
    if left:
        print("rows left behind: " + ", ".join(left))
        return 1
    print("nothing of that account remains")
    return 0


def assign(args: argparse.Namespace) -> int:
    """Give an EXISTING account a template. Adds an assignment; never creates a user, never
    sends anything, and deletes an earlier assignment only with --replace, in which case the
    answers on it are kept by the history triggers as "deleted" records."""
    admin = Admin()
    user = admin.find_user(args.email)
    if user is None:
        sys.exit(f"no auth user with address {args.email}; this command never creates one")
    user_id = user["id"]
    status, template = admin.rest(f"discovery_templates?slug=eq.{args.template}&select=id,title")
    if status != 200 or not template:
        sys.exit(f"template {args.template!r} is not loaded; run the seed first")
    template_id = template[0]["id"]

    status, profile = admin.rest(f"profiles?user_id=eq.{user_id}&select=role,first_name")
    role = profile[0]["role"] if status == 200 and profile else None
    if role == "member":
        status, _ = admin.rest(
            f"profiles?user_id=eq.{user_id}", "PATCH", {"role": "stakeholder"}, prefer="return=minimal"
        )
        if status not in (200, 204):
            sys.exit(f"could not set the profile role: HTTP {status}")
        print("profile role member -> stakeholder")
    elif role is None:
        sys.exit("that account has no profile row; nothing changed")

    status, existing = admin.rest(
        f"discovery_assignments?user_id=eq.{user_id}&select=id,template_id,status"
    )
    existing = existing or []
    if any(a["template_id"] == template_id for a in existing):
        print(f"already assigned {template[0]['title']!r}; nothing changed")
        return 0
    if existing and args.replace:
        for old in existing:
            status, _ = admin.rest(f"discovery_assignments?id=eq.{old['id']}", "DELETE")
            if status not in (200, 204):
                sys.exit(f"could not remove assignment {old['id']}: HTTP {status}")
            print(f"removed assignment {old['id']} (its answers are kept as history)")
    elif existing:
        print(f"note: {len(existing)} earlier assignment(s) kept; the portal shows the newest")

    status, created = admin.rest(
        "discovery_assignments",
        "POST",
        {"user_id": user_id, "template_id": template_id},
        prefer="return=representation",
    )
    if status != 201 or not created:
        sys.exit(f"could not assign the template: HTTP {status} {created}")
    print(f"assigned {template[0]['title']!r} ({args.template}) to {args.email} (assignment {created[0]['id']})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    give = sub.add_parser("assign", help="give an existing account a template; creates nothing")
    give.add_argument("--email", required=True)
    give.add_argument("--template", required=True, help="template slug, e.g. discovery-lead")
    give.add_argument("--replace", action="store_true", help="also remove the account's earlier assignments")
    give.set_defaults(run=assign)
    make = sub.add_parser("create", help="create a throwaway stakeholder account")
    make.add_argument("--email", help="address to use; default is random at example.org")
    make.add_argument("--first-name", default="Tester")
    make.add_argument(
        "--template",
        default=None,
        help=f"template slug to assign; default {TEMPLATE_SLUG}, or phase35-calibration-test",
    )
    make.add_argument("--with-business", action="store_true", help="also provision one private business")
    make.set_defaults(run=create)
    drop = sub.add_parser("destroy", help="remove a throwaway account and everything made for it")
    drop.add_argument("--email", required=True)
    drop.add_argument("--force", action="store_true", help="also remove businesses not named by this script, and admin accounts")
    drop.set_defaults(run=destroy)
    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
