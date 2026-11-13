"""Copy the last Twitch lives and the YouTube hub state to keiko.subscriptions, and the
posted lives to guild.messages."""

from app.migrations.helpers import begin

TWITCH_COG = "notifications_twitch"
RENEWAL_TITLE = "youtube_notification"
HUB_FIELDS = ("hub_confirmed_at", "lapse_reported_at")
MESSAGE_IDS = ("guild_id", "channel_id", "message_id")


def check(db):
    """Refuse a live, a posted live or a hub stamp this file cannot map."""
    client = db.client
    problems = []

    for live in client.audit.notifications_twitch.aggregate(_lives()):
        if not _is_handle(live.get("streamer")) or live.get("started_at") is None:
            problems.append(
                f"audit.notifications_twitch {live['_id']}: no streamer or no readable "
                "last_stream_date"
            )

    for message in client.notifications.notifications_twitch.find({}):
        readable = _is_handle(message.get("streamer")) and all(
            _is_discord_id(message.get(field)) for field in MESSAGE_IDS
        )
        if not readable:
            problems.append(
                f"notifications.notifications_twitch {message['_id']}: no streamer, "
                "or a guild_id, channel_id or message_id is not a Discord id"
            )

    for row in client.audit.reminders.find(_stamped_renewals()):
        if not _is_handle(row.get("value")):
            problems.append(
                f"audit.reminders {row['_id']}: a hub stamp without a youtuber"
            )

    for row in client.audit.reminders.find(_undated_stamps()):
        problems.append(f"audit.reminders {row['_id']}: a hub stamp that is not a date")

    return problems


def up(db):
    """Write each account's subscription and each posted live once, newest date kept."""
    with begin(db) as tx:
        for live in tx.audit.notifications_twitch.aggregate(_lives()):
            tx.keiko.subscriptions.update_one(
                {"_id": f"twitch:{live['streamer']}"},
                {
                    "$max": {"state.last_live_started_at": live["started_at"]},
                    "$setOnInsert": {"platform": "twitch", "account": live["streamer"]},
                },
                upsert=True,
            )
            tx.count("twitch_lives")

        for message in tx.notifications.notifications_twitch.find({}).sort("_id", 1):
            guild_id, channel_id = str(message["guild_id"]), str(message["channel_id"])
            written = tx.guild.messages.update_one(
                {"_id": f"{guild_id}:{channel_id}:{TWITCH_COG}:{message['streamer']}"},
                {
                    "$set": {"message_id": str(message["message_id"])},
                    "$currentDate": {"updated_at": True},
                    "$setOnInsert": {
                        "guild_id": guild_id,
                        "channel_id": channel_id,
                        "cog": TWITCH_COG,
                        "subject": message["streamer"],
                    },
                },
                upsert=True,
            )
            tx.count("twitch_messages", written.upserted_id is not None)
            tx.count("twitch_messages_replaced", written.upserted_id is None)

        for handle, state in _hub_states(tx.audit.reminders.find(_stamped_renewals())):
            tx.keiko.subscriptions.update_one(
                {"_id": f"youtube:{handle}"},
                {
                    "$max": {f"state.{field}": value for field, value in state.items()},
                    "$setOnInsert": {"platform": "youtube", "account": handle},
                },
                upsert=True,
            )
            tx.count("youtube_hub_states")


def _lives():
    return [
        {
            "$project": {
                "streamer": 1,
                "started_at": {
                    "$dateFromString": {
                        "dateString": "$last_stream_date",
                        "onError": None,
                        "onNull": None,
                    }
                },
            }
        }
    ]


def _stamped_renewals():
    return {
        "title": RENEWAL_TITLE,
        "$or": [{field: {"$ne": None}} for field in HUB_FIELDS],
    }


def _undated_stamps():
    return {
        "title": RENEWAL_TITLE,
        "$or": [
            {field: {"$exists": True, "$not": {"$type": ["date", "null"]}}}
            for field in HUB_FIELDS
        ],
    }


def _hub_states(rows):
    newest = {}

    for row in rows:
        state = newest.setdefault(row["value"], {})
        for field in HUB_FIELDS:
            stamp = row.get(field)
            if stamp is not None and (state.get(field) is None or stamp > state[field]):
                state[field] = stamp

    for state in newest.values():
        confirmed, reported = (
            state.get("hub_confirmed_at"),
            state.get("lapse_reported_at"),
        )
        if confirmed is not None and reported is not None and reported < confirmed:
            del state["lapse_reported_at"]

    return newest.items()


def _is_handle(value):
    return isinstance(value, str) and value != ""


def _is_discord_id(value):
    return isinstance(value, int) or (isinstance(value, str) and value.isdigit())
