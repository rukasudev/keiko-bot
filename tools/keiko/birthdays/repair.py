"""Repair birthdays that were saved without a reminder, now, from a terminal.

The bot repairs these on its own loop once deployed. This exists for the gap
before that: a birthday due in days cannot wait for a release window.

Dry run is the default and the only mode that needs no argument. Nothing is
created and nothing is written until `--apply` is passed, and the plan printed
by a dry run is exactly what `--apply` then executes.
"""
import argparse
import os
import sys
from typing import Any, Dict, List

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


def connect_readonly(mongo_url: str) -> None:
    """Just enough wiring to read birthdays. Deliberately not `create_app`.

    `create_app` calls `ensure_indexes`, which writes. A command whose default
    mode is "show me what you would do" must not touch the database to answer.
    """
    import certifi
    from pymongo import MongoClient

    import app

    app.mongo_client = MongoClient(
        mongo_url, tls=True, tlsCAFile=certifi.where()
    )


def mongo_url_from_environment() -> str:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(REPO_ROOT, ".env"), override=False)

    url = os.getenv("MONGO_URL_PROD") or os.getenv("MONGO_URL")
    if not url:
        raise SystemExit(
            "Set MONGO_URL_PROD (or MONGO_URL) so the plan can be read."
        )
    return url


def mongo_host(url: str) -> str:
    """The host only. The URL carries credentials and must never be printed."""
    if not url:
        return "unknown"
    tail = url.split("@")[-1] if "@" in url else url.split("//")[-1]
    return tail.split("/")[0]


REMINDER_SECRETS = (
    "REMINDER_API_KEY",
    "REMINDER_APPLICATION_ID",
    "REMINDER_AUTH_PASSWORD",
    "WEBHOOK_URL",
)


def load_for_apply(environment: str):
    """The smallest wiring that can create a reminder and store its id.

    Deliberately not `create_app`: that pulls every secret out of SSM, opens
    Redis, builds the Discord client and runs `ensure_indexes`, none of which
    this needs. It also loads `.env` with `override=True`, so the environment
    asked for on the command line lost to whatever the file said — which once
    pointed this command at the development database while it reported on
    production.
    """
    from types import SimpleNamespace

    import app
    from app.integrations.reminder_webhook import ReminderWebhook

    url = mongo_url_from_environment()
    host = mongo_host(url)
    if environment.lower() == "prod" and "localhost" in host:
        raise SystemExit(f"Refusing to run: --environment prod resolved to {host}.")

    missing = [name for name in REMINDER_SECRETS if not os.getenv(name)]
    if missing:
        raise SystemExit(
            "Missing "
            + ", ".join(missing)
            + ".\nPull them once with:\n"
            + "\n".join(
                f"  export {name}=$(aws ssm get-parameter --name /keiko/reminder/"
                f"{name.replace('REMINDER_', '').lower()} --with-decryption "
                f"--region sa-east-1 --query Parameter.Value --output text)"
                for name in missing if name != "WEBHOOK_URL"
            )
            + ("\n  export WEBHOOK_URL=$(aws ssm get-parameter --name /keiko/webhook/url"
               " --region sa-east-1 --query Parameter.Value --output text)"
               if "WEBHOOK_URL" in missing else "")
        )

    connect_readonly(url)

    # `app.services.reminders_birthdays` imports `app.components.buttons`, which
    # imports `app.services.cache`, which reads `app.redis_client` at import
    # time. The repair never touches Redis, so it gets a stand-in that shouts if
    # anything ever does rather than silently dropping a write.
    class _NoRedis:
        def __getattr__(self, name):
            def refuse(*_args, **_kwargs):
                raise RuntimeError(
                    f"The repair tool does not have Redis, and something called "
                    f"redis_client.{name}()."
                )

            return refuse

    app.redis_client = _NoRedis()

    config = SimpleNamespace(
        WEBHOOK_URL=os.getenv("WEBHOOK_URL"),
        REMINDER_APPLICATION_ID=os.getenv("REMINDER_APPLICATION_ID"),
        REMINDER_API_KEY=os.getenv("REMINDER_API_KEY"),
        REMINDER_AUTH_PASSWORD=os.getenv("REMINDER_AUTH_PASSWORD"),
        is_dev=lambda: False,
        is_prod=lambda: environment.lower() == "prod",
    )
    stand_in = SimpleNamespace(config=config)
    stand_in.reminder = ReminderWebhook(stand_in)
    app.bot = stand_in

    print(f"Environment: {environment.lower()}\nDatabase:    {host}\n")


def plan() -> List[Dict[str, Any]]:
    from app.data import birthdays as birthdays_data

    seen = set()
    entries = []

    for item in birthdays_data.find_birthdays_missing_reminder(limit=500):
        guild_id, mm_dd = item.get("guild_id"), item.get("date")
        if not guild_id or not mm_dd or (guild_id, mm_dd) in seen:
            continue
        seen.add((guild_id, mm_dd))

        config = birthdays_data.find_birthday_config(guild_id) or {}
        entries.append({
            "guild_id": guild_id,
            "date": mm_dd,
            "people": birthdays_data.count_birthday_items_by_guild_and_date(guild_id, mm_dd),
            "timezone": config.get("timezone"),
            "notification_time": config.get("notification_time"),
            "configured": bool(config),
        })

    return entries


def describe(entries: List[Dict[str, Any]]) -> None:
    if not entries:
        print("Nothing to repair: every birthday points at a reminder.")
        return

    print(f"{len(entries)} reminder(s) would be created:\n")
    print(f"{'guild':<22} {'date':<7} {'people':>6}  schedule")
    for entry in entries:
        schedule = (
            f"{entry['timezone']} {entry['notification_time'] or ''}".strip()
            if entry["configured"] else "NO CONFIG - will be skipped"
        )
        print(
            f"{entry['guild_id']:<22} {entry['date']:<7} {entry['people']:>6}  {schedule}"
        )


def apply_repair() -> int:
    from app.services import reminders_birthdays as birthdays_service

    return birthdays_service.reconcile_missing_reminders(limit=500)


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.keiko birthdays repair")
    parser.add_argument(
        "--apply", action="store_true",
        help="actually create the reminders (default is a dry run)",
    )
    parser.add_argument(
        "--environment", default="prod",
        help="which configuration to load (default: prod)",
    )
    args = parser.parse_args(argv)

    if args.apply:
        load_for_apply(args.environment)
    else:
        connect_readonly(mongo_url_from_environment())

    entries = plan()
    describe(entries)

    if not args.apply:
        print("\nDry run. Re-run with --apply to create these.")
        return 0

    repairable = [entry for entry in entries if entry["configured"]]
    if not repairable:
        return 0

    print(f"\nCreating {len(repairable)} reminder(s)...")
    repaired = apply_repair()
    print(f"Done: {repaired} created.")

    remaining = plan()
    if remaining:
        print(f"Still pending: {len(remaining)}. Check the log for the API's answer.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
