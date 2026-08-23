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


def load_app(environment: str):
    """`tools` may import `app`; the reverse is what the boundary test forbids."""
    os.environ.setdefault("APPLICATION_ENVIRONMENT", environment)

    from app import create_app
    from app.config import AppConfig

    config = AppConfig()
    create_app(config)
    return config


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

    load_app(args.environment)
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
