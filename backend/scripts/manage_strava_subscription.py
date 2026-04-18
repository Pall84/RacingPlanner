"""CLI tool for managing the Strava push-subscription.

A Strava app can have at most ONE push subscription at a time. You run
this once after deploy to register the webhook callback URL, and rarely
again unless the URL changes or you need to rotate the verify token.

Usage
-----
    # List the current subscription (if any)
    python backend/scripts/manage_strava_subscription.py list

    # Create a new subscription pointing at the deployed backend
    python backend/scripts/manage_strava_subscription.py create \\
        --callback-url https://racingplanner-api.onrender.com/api/webhooks/strava

    # Delete an existing subscription (stops all webhook events)
    python backend/scripts/manage_strava_subscription.py delete <subscription_id>

Env vars required (from `.env` or shell): STRAVA_CLIENT_ID,
STRAVA_CLIENT_SECRET, STRAVA_WEBHOOK_VERIFY_TOKEN. Load them from the
same .env the backend uses.

How subscription creation works
-------------------------------
Strava's create endpoint immediately performs the GET handshake against
the callback_url you provide, so the deployed app must already have the
matching verify_token set in Render before running `create`. If Render's
env var isn't set or is different from the one in your local .env, the
create request will fail with a 400 and Strava won't retry.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    import httpx
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dev-only script
    # dotenv is optional; skip if not installed.
    httpx = None

SUBSCRIPTIONS_URL = "https://www.strava.com/api/v3/push_subscriptions"


def _load_env() -> tuple[str, str, str]:
    # Look for .env in the backend dir (same one the backend reads).
    backend_env = Path(__file__).resolve().parents[1] / ".env"
    if backend_env.exists():
        try:
            from dotenv import load_dotenv  # noqa: I001
            load_dotenv(backend_env)
        except ImportError:
            # Fallback: read KEY=VALUE lines ourselves. Keeps this script
            # runnable without python-dotenv installed locally.
            for line in backend_env.read_text().splitlines():
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    client_id = os.environ.get("STRAVA_CLIENT_ID", "")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "")
    verify_token = os.environ.get("STRAVA_WEBHOOK_VERIFY_TOKEN", "")

    missing = [
        name for name, val in [
            ("STRAVA_CLIENT_ID", client_id),
            ("STRAVA_CLIENT_SECRET", client_secret),
            ("STRAVA_WEBHOOK_VERIFY_TOKEN", verify_token),
        ] if not val
    ]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    return client_id, client_secret, verify_token


def cmd_list() -> None:
    client_id, client_secret, _ = _load_env()
    resp = httpx.get(
        SUBSCRIPTIONS_URL,
        params={"client_id": client_id, "client_secret": client_secret},
        timeout=15,
    )
    resp.raise_for_status()
    subs = resp.json()
    if not subs:
        print("No subscriptions for this Strava app.")
        return
    for s in subs:
        print(f"id={s['id']}  callback={s['callback_url']}  created={s.get('created_at')}")


def cmd_create(callback_url: str) -> None:
    client_id, client_secret, verify_token = _load_env()
    print(f"Creating subscription → {callback_url}")
    print("Strava will do a GET handshake against this URL right now. It must "
          "be reachable and the deployed app must have the SAME verify token.")
    resp = httpx.post(
        SUBSCRIPTIONS_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "callback_url": callback_url,
            "verify_token": verify_token,
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        print(f"FAILED ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    print(f"OK — subscription id={data.get('id')}")


def cmd_delete(sub_id: int) -> None:
    client_id, client_secret, _ = _load_env()
    resp = httpx.delete(
        f"{SUBSCRIPTIONS_URL}/{sub_id}",
        params={"client_id": client_id, "client_secret": client_secret},
        timeout=15,
    )
    if resp.status_code >= 400:
        print(f"FAILED ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)
    print(f"Deleted subscription id={sub_id}")


def main() -> None:
    if httpx is None:
        print("ERROR: httpx is required (`pip install httpx`)", file=sys.stderr)
        sys.exit(2)

    parser = argparse.ArgumentParser(description="Manage Strava push subscription")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List existing subscriptions")

    create_p = sub.add_parser("create", help="Create a new subscription")
    create_p.add_argument(
        "--callback-url",
        required=True,
        help="Public HTTPS URL of the webhook endpoint, e.g. "
             "https://racingplanner-api.onrender.com/api/webhooks/strava",
    )

    delete_p = sub.add_parser("delete", help="Delete a subscription by id")
    delete_p.add_argument("sub_id", type=int)

    args = parser.parse_args()

    if args.cmd == "list":
        cmd_list()
    elif args.cmd == "create":
        cmd_create(args.callback_url)
    elif args.cmd == "delete":
        cmd_delete(args.sub_id)


if __name__ == "__main__":
    main()
