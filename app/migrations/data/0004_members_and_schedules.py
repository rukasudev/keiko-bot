"""Copy the birthdays into guild.members, their reminders and renewals to schedules."""

from app.migrations.helpers import begin, insert_missing

RENEWAL_TITLE = "youtube_notification"
BIRTHDAY_FIELDS = ("date", "month", "day", "message", "image", "self_edit_count")
DAYS_IN_MONTH = {
    1: 31,
    2: 29,
    3: 31,
    4: 30,
    5: 31,
    6: 30,
    7: 31,
    8: 31,
    9: 30,
    10: 31,
    11: 30,
    12: 31,
}


def valid_date(value):
    """Whether `value` is a birthday date as Keiko stores it, `MM-DD`."""
    if not isinstance(value, str) or len(value) != 5 or value[2] != "-":
        return False
    month, day = value[:2], value[3:]
    if not (month.isdigit() and day.isdigit()):
        return False
    return 1 <= int(month) <= 12 and 1 <= int(day) <= DAYS_IN_MONTH[int(month)]


def member_document(row):
    """The guild.members document of one reminders.birthdays row."""
    guild_id, user_id, date = str(row["guild_id"]), str(row["user_id"]), row["date"]
    birthday = {field: row[field] for field in BIRTHDAY_FIELDS if field in row}
    birthday.setdefault("month", int(date[:2]))
    birthday.setdefault("day", int(date[3:]))
    state = {}
    if "celebrated_year" in row:
        state["reminders_birthday"] = {"last_celebrated_year": row["celebrated_year"]}
    document = {
        "_id": f"{guild_id}:{user_id}",
        "guild_id": guild_id,
        "user_id": user_id,
        "options": {"reminders_birthday": birthday},
        "state": state,
    }
    for stamp in ("created_at", "updated_at"):
        if stamp in row:
            document[stamp] = row[stamp]
    return document


def members_from(rows):
    """One member per server and user, the newest row winning with the latest year any
    row celebrated, and how many rows lost."""
    chosen, years, duplicates = {}, {}, 0
    for row in rows:
        document = member_document(row)
        if document["_id"] in chosen:
            duplicates += 1
        chosen[document["_id"]] = document
        year = (
            document["state"].get("reminders_birthday", {}).get("last_celebrated_year")
        )
        if isinstance(year, int):
            years[document["_id"]] = max(year, years.get(document["_id"], year))
    for member_key, year in years.items():
        celebrated = chosen[member_key]["state"].setdefault("reminders_birthday", {})
        celebrated["last_celebrated_year"] = year
    return list(chosen.values()), duplicates


def birthday_schedules(rows, members):
    """One schedule per server and date its members keep, holding the first reminder id
    any row of that date carried, and how many other ids were left out."""
    kept = {
        (member["guild_id"], member["options"]["reminders_birthday"]["date"])
        for member in members
    }
    reminders, created = {}, {}
    for row in rows:
        key = (str(row["guild_id"]), row["date"])
        if row.get("reminder_id") is not None:
            ids = reminders.setdefault(key, [])
            if str(row["reminder_id"]) not in ids:
                ids.append(str(row["reminder_id"]))
        if row.get("created_at") is not None and key not in created:
            created[key] = row["created_at"]

    schedules, extras = [], 0
    for guild_id, date in sorted(kept):
        ids = reminders.get((guild_id, date), [])
        extras += max(len(ids) - 1, 0)
        schedule = {
            "_id": f"birthday:{guild_id}:{date}",
            "kind": "birthday",
            "reminder_id": ids[0] if ids else None,
            "due_at": None,
        }
        if (guild_id, date) in created:
            schedule["created_at"] = created[(guild_id, date)]
        schedules.append(schedule)
    return schedules, extras


def renewal_schedules(rows):
    """One schedule per youtuber from the renewal rows, the first row's reminder id as
    text and its expiry as it was, and how many rows were left out."""
    schedules, extras, without_reminder = {}, 0, 0
    for row in rows:
        if row.get("reminder_id") is None:
            without_reminder += 1
            continue
        schedule_id = f"youtube_renewal:{row['value']}"
        if schedule_id in schedules:
            extras += 1
            continue
        schedule = {
            "_id": schedule_id,
            "kind": "youtube_renewal",
            "reminder_id": str(row["reminder_id"]),
            "due_at": None,
        }
        for field in ("created_at", "expires_at"):
            if row.get(field) is not None:
                schedule[field] = row[field]
        schedules[schedule_id] = schedule
    return list(schedules.values()), extras, without_reminder


def problems_in(birthdays, renewals):
    """What the rows hold that this file does not know how to copy."""
    problems = []
    for row in birthdays:
        if not row.get("guild_id") or not row.get("user_id"):
            problems.append(
                f"reminders.birthdays {row.get('_id')} names no server or user"
            )
        elif not valid_date(row.get("date")):
            problems.append(
                f"reminders.birthdays {row.get('_id')} has the date {row.get('date')!r}"
            )
    for row in renewals:
        if row.get("title") != RENEWAL_TITLE:
            problems.append(
                f"audit.reminders {row.get('_id')} has the title {row.get('title')!r}"
            )
        elif row.get("reminder_id") is not None and not row.get("value"):
            problems.append(f"audit.reminders {row.get('_id')} names no youtuber")
    return problems


def check(db):
    """Refuse a birthday without its server, user or date, or a row not a renewal."""
    birthdays = db.client.reminders.birthdays.find(
        {}, {"guild_id": 1, "user_id": 1, "date": 1}
    )
    renewals = db.client.audit.reminders.find(
        {}, {"title": 1, "value": 1, "reminder_id": 1}
    )
    return problems_in(list(birthdays), list(renewals))


def up(db):
    """Write each member, birthday schedule and renewal schedule not there yet."""
    with begin(db) as tx:
        rows = list(tx.reminders.birthdays.find({}).sort("_id", 1))
        members, duplicates = members_from(rows)
        birthdays, birthday_extras = birthday_schedules(rows, members)
        renewal_rows = list(
            tx.audit.reminders.find({"title": RENEWAL_TITLE}).sort("_id", 1)
        )
        renewals, renewal_extras, without_reminder = renewal_schedules(renewal_rows)

        tx.count("members_created", insert_missing(tx.guild.members, members))
        tx.count("birthday_rows_merged", duplicates)
        tx.count(
            "birthday_schedules_created", insert_missing(tx.keiko.schedules, birthdays)
        )
        tx.count(
            "birthday_schedules_without_reminder",
            sum(1 for schedule in birthdays if schedule["reminder_id"] is None),
        )
        tx.count("birthday_reminders_left_out", birthday_extras)
        tx.count(
            "renewal_schedules_created", insert_missing(tx.keiko.schedules, renewals)
        )
        tx.count("renewal_rows_left_out", renewal_extras)
        tx.count("renewal_rows_without_reminder", without_reminder)
