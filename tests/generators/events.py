"""Event documents as `analytics.record` writes them, for tests that seed the three
event collections directly."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from app.data import data, wait
from app.core import analytics

BLOCK_LINKS = "block_links"
LINK_PROPS = ("channel_id", "link", "host", "mode", "reason", "rule", "match", "deleted")


def event(
    event_type: str,
    *,
    guild_id: Optional[str] = None,
    cog: Optional[str] = None,
    actor: Optional[Mapping[str, Any]] = None,
    props: Optional[Mapping[str, Any]] = None,
    session_id: Optional[str] = None,
    ts: Optional[datetime] = None,
) -> Dict[str, Any]:
    """One envelope, with the fields `record` fills in."""
    return {
        "ts": ts or datetime.now(timezone.utc),
        "type": event_type,
        "guild_id": guild_id,
        "cog": cog,
        "actor": dict(actor or {"kind": "user", "id": None}),
        "trace_id": None,
        "session_id": session_id,
        "props": dict(props or {}),
        "v": 1,
    }


def blocked_link(row: Mapping[str, Any]) -> Dict[str, Any]:
    """A `link.blocked` event from the fields a blocked-link row used to hold."""
    return event(
        "link.blocked",
        guild_id=row.get("guild_id"),
        cog=BLOCK_LINKS,
        actor={"kind": "user", "id": row.get("user_id")},
        props={name: row[name] for name in LINK_PROPS if name in row},
        ts=row.get("created_at"),
    )


def insert_blocked_link(row: Mapping[str, Any]) -> None:
    """Seed one blocked link the way the sink writes it."""
    wait(data().events_activity.insert_many([blocked_link(row)]))


def blocked_links(guild_id: str) -> List[Dict[str, Any]]:
    """A server's blocked links, newest first, as the records list reads them."""
    return wait(data().events_activity.find_by_guild(guild_id, cog=BLOCK_LINKS, types=["link.blocked"]))


def record_event(
    event_type: str,
    *,
    guild_id: Any = None,
    user_id: Any = None,
    feature: Optional[str] = None,
    session_id: Optional[str] = None,
    **props: Any,
) -> Optional[Dict[str, Any]]:
    """`analytics.record` with the server, the member, the cog and the session named the
    way a test reads them, every other keyword a prop."""
    return analytics.record(
        event_type,
        guild_id=guild_id,
        cog=feature,
        actor=analytics.actor("user", user_id) if user_id else None,
        props=props,
        session_id=session_id,
    )


def stored(guild_id: str) -> List[Dict[str, Any]]:
    """Every event a server has in the three collections, newest first."""
    found = [
        *wait(data().events_changes.find_by_guild(guild_id)),
        *wait(data().events_activity.find_by_guild(guild_id)),
        *wait(data().events_interactions.find_by_guild(guild_id)),
    ]
    return sorted(found, key=lambda document: document["ts"], reverse=True)


def age_events(mongo_client: Any, moment: datetime, session_id: Optional[str] = None) -> None:
    """Move the stored events, or one session's, to `moment` in the in-memory database."""
    for name in ("changes", "activity", "interactions"):
        for document in mongo_client.events[name]._data:
            if session_id is None or document.get("session_id") == session_id:
                document["ts"] = moment


def lifecycle(mongo_client: Any, guild_id: str, cog: str) -> List[str]:
    """The lifecycle keys of a cog's changes in a server, in the order they were written."""
    from app.core.cogs import lifecycle_key

    return [
        lifecycle_key(document["type"])
        for document in mongo_client.events.changes.find({"guild_id": guild_id, "cog": cog})
    ]
