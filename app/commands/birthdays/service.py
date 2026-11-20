"""Birthday reminders: the members' dates, their reminders, the panel and the save."""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, TypedDict

import discord

from app import logger
from app.constants import Commands as commands_constants
from app.constants import KeikoIcons
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants as view_constants
from app.core import members
from app.core.cache import remove_cog_cache_by_guild
from app.core.calendar import is_valid_mm_dd, parse_mm_dd
from app.core.cogs import is_feature_on
from app.core.dates import (
    format_mm_dd_label,
    get_month_label,
    next_mm_dd_occurrence,
    utc_today,
)
from app.core.facts import Facts, facts
from app.core.feature_health import Figure, figure_lines
from app.core.mentions import mention_of
from app.core.servers import save_server_setting, server_setting, set_feature_enabled
from app.core.threads import to_thread
from app.core.utils import (
    fill_placeholders,
    ml,
    parse_locale,
    values_of,
)
from app.data import data
from app.data.collection import Document
from app.data.keiko import schedules as schedules_data
from app.exceptions import ServerSettingsMissing
from app.forms import document_values, item_values, open_feature
from app.forms.features.feature import (
    AsideAction,
    CommitContext,
    CommitResult,
    DuplicateItem,
    GenericCogFeature,
    ListChanged,
    OpenContext,
    Opened,
)
from app.forms.form.form_yaml import CardStep
from app.forms.form.manager import FoundItem, PanelExtras, PanelPage
from app.forms.form.responses.styles import format_value
from app.forms.form.responses.summary import PanelRow, option_label
from app.integrations.reminders import schedule as reminders_service
from app.ui.embed import announcement_embed
from app.ui.message_preview import MessagePreviewView


def command_facts(**values: Any) -> Facts:
    """The facts this command's code passes its copy, each under its address."""
    return facts("reminders_birthday", **values)


class MessageChoice(TypedDict):
    """A celebration message as saved: Keiko's own, or a custom title and content."""

    mode: str
    title: str | None
    content: str | None


class ImageChoice(TypedDict):
    """A celebration image as saved: Keiko's own, or a custom url."""

    mode: str
    url: str | None


class BirthdayDocument(TypedDict):
    """A member's birthday as Keiko saves it."""

    date: str
    month: int
    day: int
    self_edit_count: int
    message: MessageChoice
    image: ImageChoice


class CompositionField(TypedDict, total=False):
    """One field of a saved birthday as the form's composition shows it."""

    value: Any
    title: str
    style: str
    hidden: bool


@dataclass(frozen=True)
class FormBirthday:
    """A member's birthday as a setup or edit form answered it."""

    user_id: str
    mm_dd: str
    message: MessageChoice
    image: ImageChoice


@dataclass(frozen=True)
class BirthdayStats:
    """A server's birthday figures: months and dates paired with how many fall there."""

    total: int
    this_month: int
    busiest_month: tuple[int, int] | None
    quietest_month: tuple[int, int] | None
    most_common_date: tuple[str, int] | None


async def manager(interaction: discord.Interaction) -> None:
    """The slash command: the setup form, or the manager of what is saved."""
    await open_feature(interaction, "reminders_birthday")


def _mb(key: str, locale: str) -> str:
    return str(
        ml(
            f"reminders_birthday.{key}",
            locale=locale,
        )
    )


async def birthday_config(guild_id: str) -> Mapping[str, Any] | None:
    """The saved birthday configuration of a guild, or nothing."""
    return await data().guild_cogs.find_cog_by_guild_id(guild_id, "reminders_birthday")


async def save_birthday_config(
    guild_id: str,
    channel_id: str,
    mention: str = "none",
    notification_time: str | None = None,
    default_message: MessageChoice | None = None,
    updated_by: str | None = None,
    mention_roles: Sequence[str] = (),
) -> Mapping[str, Any]:
    """Save the birthday configuration over the saved one, returning it as saved;
    a mention it does not know pings nobody."""
    chosen = mention_of(mention, list(mention_roles))
    options: dict[str, Any] = {
        "channel": str(channel_id),
        "mention": chosen.kind,
        "mention_roles": list(chosen.roles),
    }
    if notification_time is not None:
        options["notification_time"] = str(notification_time)
    if default_message is not None:
        options["default_message"] = default_message

    key = "reminders_birthday"
    await data().guild_cogs.insert_cog_by_guild_id(
        key, {"guild_id": str(guild_id), **options}, updated_by
    )
    await to_thread(remove_cog_cache_by_guild, str(guild_id), key)
    return await birthday_config(guild_id) or {}


async def accepts_birthdays(guild_id: str) -> bool:
    """Whether a guild takes birthdays: it is configured and its feature is on."""
    return is_feature_on(await birthday_config(guild_id))


def default_birthday(mm_dd: str) -> BirthdayDocument:
    """A birthday on `mm_dd` with Keiko's message and image."""
    month, day = [int(part) for part in str(mm_dd).split("-")]
    return {
        "date": str(mm_dd),
        "month": month,
        "day": day,
        "self_edit_count": 0,
        "message": {"mode": "default", "title": None, "content": None},
        "image": {"mode": "default", "url": None},
    }


async def find_birthday(guild_id: str, user_id: str) -> Mapping[str, Any] | None:
    """The member's birthday, with what Keiko keeps about them under `state`."""
    return await data().guild_members.find_member_option(
        guild_id, user_id, "reminders_birthday"
    )


async def find_birthdays_by_guild(guild_id: str) -> Sequence[Mapping[str, Any]]:
    """Every birthday saved in a server."""
    return await data().guild_members.find_members_by_option(
        guild_id, "reminders_birthday"
    )


async def find_birthdays_on(guild_id: str, mm_dd: str) -> Sequence[Mapping[str, Any]]:
    """The server's birthdays on one date."""
    return await data().guild_members.find_members_by_option(
        guild_id, "reminders_birthday", {"date": str(mm_dd)}
    )


async def count_birthdays_on(guild_id: str, mm_dd: str) -> int:
    """How many of the server's birthdays fall on one date."""
    return await data().guild_members.count_members_by_option(
        guild_id, "reminders_birthday", {"date": str(mm_dd)}
    )


async def save_birthday(
    guild_id: str,
    user_id: str,
    mm_dd: str,
    self_edit_count: int | None = None,
    message: MessageChoice | None = None,
    image: ImageChoice | None = None,
    display_name: str | None = None,
) -> Mapping[str, Any]:
    """Save the member's birthday, keeping what was saved where nothing new is given."""
    existing = await find_birthday(guild_id, user_id) or {}
    birthday = default_birthday(mm_dd)
    birthday["self_edit_count"] = (
        self_edit_count
        if self_edit_count is not None
        else existing.get("self_edit_count", 0)
    )
    birthday["message"] = message or existing.get("message") or birthday["message"]
    birthday["image"] = image or existing.get("image") or birthday["image"]

    return await data().guild_members.set_member_option(
        guild_id,
        user_id,
        "reminders_birthday",
        birthday,
        display_name,
    )


def last_celebrated_year(item: Mapping[str, Any]) -> int | None:
    """The year Keiko last celebrated the member of a saved birthday."""
    year: int | None = item["state"].get("last_celebrated_year")
    return year


async def claim_birthday_year(
    guild_id: str, user_id: str, year: int, display_name: str | None = None
) -> bool:
    """Record this year's celebration of a member; False when already recorded."""
    return await data().guild_members.claim_member_state(
        guild_id,
        user_id,
        "reminders_birthday",
        "last_celebrated_year",
        int(year),
        display_name,
    )


async def release_birthday_year(
    guild_id: str, user_id: str, year: int, previous: int | None
) -> bool:
    """Give back this year's claim of a member whose message was not posted."""
    return await data().guild_members.restore_member_state(
        guild_id,
        user_id,
        "reminders_birthday",
        "last_celebrated_year",
        int(year),
        previous,
    )


def get_self_edit_count(item: Mapping[str, Any] | None) -> int:
    """How many times the member changed their own birthday, zero when unreadable."""
    if not item:
        return 0

    try:
        return int(item.get("self_edit_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def can_self_edit_birthday(item: Mapping[str, Any] | None) -> bool:
    """Whether the member may still change their own birthday."""
    return get_self_edit_count(item) < commands_constants.SELF_BIRTHDAY_EDIT_LIMIT


async def upsert_birthday(
    guild_id: str,
    user_id: str,
    mm_dd: str,
    increment_self_edit: bool = False,
    message: MessageChoice | None = None,
    image: ImageChoice | None = None,
    display_name: str | None = None,
) -> Mapping[str, Any]:
    """Save the member's birthday on `mm_dd` with its reminder, freeing the old date."""
    existing = await find_birthday(guild_id, user_id)
    old_date = existing.get("date") if existing else None
    self_edit_count = get_self_edit_count(existing) + (
        1 if existing and increment_self_edit else 0
    )

    await schedule_birthday_date(guild_id, mm_dd)
    try:
        item = await save_birthday(
            guild_id,
            user_id,
            mm_dd,
            self_edit_count=self_edit_count,
            message=message,
            image=image,
            display_name=display_name,
        )
    except Exception:
        await release_birthday_date(guild_id, mm_dd)
        raise

    if old_date and old_date != mm_dd:
        await release_birthday_date(guild_id, old_date)
    return item


def birthday_schedule_id(guild_id: str, mm_dd: str) -> str:
    """The schedule of one server's birthdays on one date."""
    return schedules_data.schedule_id("birthday", guild_id, mm_dd)


async def birthday_schedules_by_reminder(reminder_id: str) -> list[Document]:
    """Every server's birthday schedule one reminder serves."""
    return await data().keiko_schedules.find_schedules_by_reminder(
        reminder_id, "birthday"
    )


async def birthday_schedules_missing_reminder(limit: int) -> list[Document]:
    """Birthday schedules whose reminder could not be created yet, soonest due first."""
    return await data().keiko_schedules.find_schedules_missing_reminder(
        "birthday", limit
    )


async def schedule_birthday_date(guild_id: str, mm_dd: str) -> None:
    """Give a server's birthdays on a date their reminder, unless they have one."""
    schedule_id = birthday_schedule_id(guild_id, mm_dd)
    schedule = await data().keiko_schedules.find_schedule(schedule_id)
    if schedule and schedule.get("reminder_id"):
        return

    config = await birthday_config(guild_id) or {}
    zone = await _server_zone(guild_id)
    notification_time = config.get("notification_time")
    reminder_id = await _create_date_reminder(mm_dd, zone, notification_time)
    await data().keiko_schedules.upsert_schedule(
        schedule_id,
        "birthday",
        reminder_id,
        due_at=next_birthday_due(mm_dd, zone, notification_time),
    )


async def _create_date_reminder(
    mm_dd: str, zone: str, notification_time: str | None
) -> str | None:
    return await to_thread(
        reminders_service.create_reminder,
        commands_constants.REMINDER_API_TITLE_BIRTHDAY,
        mm_dd,
        notes=mm_dd,
        timezone_name=zone,
        notification_time=notification_time,
    )


async def release_birthday_date(guild_id: str, mm_dd: str) -> None:
    """Drop a date's schedule once no member of the server has it, and its reminder once
    no server's schedule uses it."""
    if await count_birthdays_on(guild_id, mm_dd) > 0:
        return

    schedule_id = birthday_schedule_id(guild_id, mm_dd)
    schedule = await data().keiko_schedules.find_schedule(schedule_id)
    if not schedule:
        return

    await data().keiko_schedules.delete_schedule(schedule_id)
    reminder_id = schedule.get("reminder_id")
    if not reminder_id:
        return

    remaining = len(await birthday_schedules_by_reminder(reminder_id))
    await to_thread(
        reminders_service.cleanup_reminder_if_unused, reminder_id, lambda: remaining
    )


def next_birthday_due(mm_dd: str, zone: str, notification_time: str | None) -> datetime:
    """When the reminder of a date fires next, in UTC."""
    occurrence = reminders_service.next_mm_dd_occurrence_for_timezone(
        mm_dd, zone, notification_time
    )
    return occurrence.astimezone(timezone.utc)


async def reconcile_missing_reminders(limit: int = 50) -> int:
    """Create the reminders that a failed save left behind, releasing the schedules no
    configuration or member needs; how many it created."""
    repaired = 0

    for schedule in await birthday_schedules_missing_reminder(limit):
        guild_id, mm_dd = schedules_data.schedule_subject(schedule)
        config = await birthday_config(guild_id)
        if not config or not await count_birthdays_on(guild_id, mm_dd):
            await data().keiko_schedules.delete_schedule_without_reminder(
                schedule["_id"]
            )
            continue

        zone = await _server_zone(guild_id)
        notification_time = config.get("notification_time")
        try:
            reminder_id = await _create_date_reminder(mm_dd, zone, notification_time)
        except Exception as error:
            logger.warn(
                f"Reminder reconciliation failed for guild {guild_id} on {mm_dd}: "
                f"{type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            continue

        if not reminder_id:
            continue

        await data().keiko_schedules.upsert_schedule(
            schedule["_id"],
            "birthday",
            reminder_id,
            due_at=next_birthday_due(mm_dd, zone, notification_time),
        )
        repaired += 1

    return repaired


async def remove_birthday(guild_id: str, user_id: str) -> Mapping[str, Any] | None:
    """Forget the member's birthday and free its date; the birthday it held."""
    item = await data().guild_members.unset_member_option(
        guild_id, user_id, "reminders_birthday"
    )
    if item:
        await release_birthday_date(guild_id, item["date"])
    return item


async def get_upcoming_birthdays(
    guild_id: str, limit: int = 3, today: date | None = None
) -> list[Mapping[str, Any]]:
    """The server's next `limit` birthdays counted from `today`."""
    today = today or utc_today()
    items = await find_birthdays_by_guild(guild_id)
    return sorted(items, key=lambda item: next_mm_dd_occurrence(item["date"], today))[
        :limit
    ]


async def get_birthday_stats(guild_id: str, today: date | None = None) -> BirthdayStats:
    """How the server's birthdays spread over months and dates, counted from `today`."""
    today = today or utc_today()
    items = await find_birthdays_by_guild(guild_id)
    month_counts = Counter(item["month"] for item in items)
    date_counts = Counter(item["date"] for item in items)
    return BirthdayStats(
        total=len(items),
        this_month=month_counts.get(today.month, 0),
        busiest_month=month_counts.most_common(1)[0] if month_counts else None,
        quietest_month=(
            min(month_counts.items(), key=lambda pair: pair[1])
            if month_counts
            else None
        ),
        most_common_date=date_counts.most_common(1)[0] if date_counts else None,
    )


async def birthday_manager_cog_data(
    guild_id: str, apply_defaults: bool = True
) -> Mapping[str, Any]:
    """The saved configuration keyed as the form names it, with the card's defaults
    filled only when `apply_defaults`."""
    config = await birthday_config(guild_id) or {}
    items = await find_birthdays_by_guild(guild_id)
    message = config.get("default_message") or {}
    zone = (
        await _server_zone(guild_id) if apply_defaults else await _saved_zone(guild_id)
    )
    return {
        "guild_id": str(guild_id),
        commands_constants.ENABLED_KEY: is_feature_on(config),
        "channel": {
            "style": "channel",
            "values": str(config.get("channel")) if config.get("channel") else None,
        },
        "mention": (
            mention_of(config.get("mention"), config.get("mention_roles")).kind
            if apply_defaults
            else config.get("mention")
        ),
        "mention_roles": {
            "style": "role",
            "values": list(config.get("mention_roles") or []),
        },
        "timezone": zone,
        "notification_time": config.get("notification_time"),
        "default_message_mode": (
            message.get("mode", "default") if apply_defaults else message.get("mode")
        ),
        "default_message_title": message.get("title"),
        "default_message_content": message.get("content"),
        "reminders_birthday": {
            "style": "composition",
            "values": [to_summary_composition(item) for item in items],
        },
    }


async def save_birthday_config_changes(
    guild_id: str, changes: Mapping[str, Any], updated_by: str | None = None
) -> None:
    """Apply the edited settings on top of the saved birthday configuration."""
    config_keys = {
        "channel",
        "mention",
        "mention_roles",
        "timezone",
        "notification_time",
        "default_message_mode",
        "default_message_title",
        "default_message_content",
    }
    edited = document_values(changes)
    if not config_keys.intersection(edited):
        return

    config = await birthday_config(guild_id) or {}
    mention = mention_of(
        _first(edited["mention"]) if "mention" in edited else config.get("mention"),
        edited.get("mention_roles", config.get("mention_roles")),
    )
    channel_id = _first(edited.get("channel")) or config.get("channel")
    zone = _first(edited.get("timezone")) or await _server_zone(guild_id)
    notification_time = _first(edited.get("notification_time")) or config.get(
        "notification_time"
    )

    await setup_birthdays(
        guild_id,
        str(channel_id),
        mention.kind,
        zone,
        notification_time,
        _default_message_from_changes(edited, config),
        updated_by=updated_by,
        mention_roles=mention.roles,
    )


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


async def _server_zone(guild_id: str) -> str:
    return await server_setting(guild_id, commands_constants.SERVER_TIMEZONE_KEY)


async def _saved_zone(guild_id: str) -> str | None:
    try:
        return await _server_zone(guild_id)
    except ServerSettingsMissing:
        return None


def _default_message_label(
    default_message: Mapping[str, Any] | None, locale: str
) -> str:
    mode = (default_message or {}).get("mode")
    key = "custom" if mode == "custom" else "default"
    return str(ml(f"texts.modes.{key}", locale=locale))


def _default_message_from_values(mode: Any, title: Any, content: Any) -> MessageChoice:
    custom = mode == "custom" and bool(title) and bool(content)
    return {
        "mode": "custom" if custom else "default",
        "title": str(title) if custom else None,
        "content": str(content) if custom else None,
    }


def _default_message_from_changes(
    edited: Mapping[str, Any], config: Mapping[str, Any]
) -> MessageChoice:
    current = config.get("default_message") or {}
    return _default_message_from_values(
        _first(edited.get("default_message_mode")) or current.get("mode", "default"),
        _first(edited.get("default_message_title")) or current.get("title"),
        _first(edited.get("default_message_content")) or current.get("content"),
    )


async def reschedule_birthdays(
    guild_id: str, timezone_value: str, notification_time: str | None
) -> None:
    """Move every birthday reminder of a guild to `timezone_value` at
    `notification_time`, once per reminder."""
    if not timezone_value or not notification_time:
        return

    moved = set()
    for mm_dd in sorted(
        {item["date"] for item in await find_birthdays_by_guild(guild_id)}
    ):
        schedule_id = birthday_schedule_id(guild_id, mm_dd)
        schedule = await data().keiko_schedules.find_schedule(schedule_id) or {}
        reminder_id = schedule.get("reminder_id")
        if not reminder_id:
            continue

        await data().keiko_schedules.set_schedule_due(
            schedule_id, next_birthday_due(mm_dd, timezone_value, notification_time)
        )
        if reminder_id in moved:
            continue

        moved.add(reminder_id)
        await to_thread(
            reminders_service.update_reminder,
            reminder_id,
            mm_dd,
            timezone_name=timezone_value,
            notification_time=notification_time,
        )


async def birthday_settings_rows(
    guild_id: str, locale: str, today: date | None = None
) -> list[PanelRow]:
    """The panel rows of the birthday feature, counted from `today`."""
    config = await birthday_config(guild_id) or {}
    stats = await get_birthday_stats(guild_id, today=today)
    upcoming = await get_upcoming_birthdays(guild_id, limit=3, today=today)
    upcoming_lines = [
        f"<@{item['user_id']}>: {format_mm_dd_label(item['date'], locale)}"
        for item in upcoming
    ]

    labels = _setting_labels(locale)
    mention = mention_of(config.get("mention"), config.get("mention_roles"))
    mentioned_roles = (
        [
            PanelRow(
                key="mention_roles",
                title=labels["mention_roles"],
                value=list(mention.roles),
                style="role",
            )
        ]
        if mention.roles
        else []
    )
    return [
        PanelRow(
            key="channel",
            title=_mb("command.panel.rows.channel", locale),
            value=config.get("channel"),
            style="channel",
        ),
        PanelRow(
            key="mention",
            title=labels["mention"],
            value=_mention_label(mention.kind, locale),
        ),
        *mentioned_roles,
        PanelRow(
            key="timezone",
            title=labels["timezone"],
            value=await _server_zone(guild_id),
        ),
        PanelRow(
            key="notification_time",
            title=labels["notification_time"],
            value=config.get("notification_time") or "-",
        ),
        PanelRow(
            key="default_message_mode",
            title=labels["default_message_mode"],
            value=_default_message_label(config.get("default_message"), locale),
        ),
        PanelRow(
            key="reminders_birthday",
            title=_mb("command.stats.figures.total", locale),
            value=str(stats.total),
        ),
        PanelRow(
            key="reminders_birthday",
            title=_mb("command.panel.rows.next", locale),
            value=upcoming_lines,
            style="bullet",
        ),
    ]


def _settings_card() -> CardStep:
    card = FEATURE.definition.step("birthday_config")
    if not isinstance(card, CardStep):
        raise TypeError("the birthday settings are the card birthday_config")
    return card


def _setting_labels(locale: str) -> dict[str, str]:
    """The label the settings card gives each of its keys, which its rows repeat."""
    card = _settings_card()
    return {field.key: card.label_of(field).get(locale) for field in card.fields}


def _mention_label(kind: str, locale: str) -> str:
    section = next(
        section for section in _settings_card().sections if section.key == "mention"
    )
    return option_label(section.choices(), kind, locale) or kind


async def birthday_month_pages(
    guild_id: str, locale: str, today: date | None = None
) -> list[PanelPage]:
    """The birthdays in date order, a month per page from this one, or an empty page."""
    today = today or utc_today()
    title = _mb("command.panel.pages.title", locale)
    by_month: dict[int, list[Mapping[str, Any]]] = {}
    for item in await find_birthdays_by_guild(guild_id):
        try:
            month, _day = parse_mm_dd(str(item.get("date")))
        except (TypeError, ValueError):
            continue
        by_month.setdefault(month, []).append(item)

    pages = []
    for offset in range(12):
        month = (today.month - 1 + offset) % 12 + 1
        if month not in by_month:
            continue

        lines = tuple(
            f"- <@{item['user_id']}>: {format_mm_dd_label(item['date'], locale)}"
            for item in sorted(by_month[month], key=lambda item: str(item["date"]))
        )
        pages.append(
            PanelPage(title, lines, _mb("command.panel.pages.description", locale))
        )
    return pages or [PanelPage(title, (), _mb("command.panel.pages.empty", locale))]


async def birthday_found_members(guild_id: str, locale: str) -> dict[str, FoundItem]:
    """Each listed member's birthday messages, for the screen a search opens."""
    config = await birthday_config(guild_id) or {}
    found: dict[str, FoundItem] = {}
    for item in await find_birthdays_by_guild(guild_id):
        source, title, content = message_source(item, config, locale)
        label = _mb(f"command.panel.authors.{source}", locale)
        block = format_value([title, content], "code-block", locale)
        found[str(item.get("user_id"))] = FoundItem((f"💬 **{label}**{block}",))
    return found


def render_birthday_message(
    text: str, member_mention: str, guild_name: str, mm_dd: str, locale: str
) -> str:
    """A celebration text with the member, the server and the date filled in."""
    if not text:
        return text

    return fill_placeholders(
        text,
        {
            "user": member_mention,
            "server": guild_name,
            "date": format_mm_dd_label(mm_dd, locale),
        },
    )


def birthday_default_text(key: str, locale: str) -> str:
    """Keiko's own celebration `title` or `description`, the card's default too."""
    return _mb(f"ui.announcement.defaults.{key}", locale)


def message_source(
    item: Mapping[str, Any], config: Mapping[str, Any], locale: str
) -> tuple[str, str, str]:
    """Who celebrates the member (`member`, `server` or `keiko`), and the text."""
    message = item.get("message") or {}
    if (
        message.get("mode") == "custom"
        and message.get("title")
        and message.get("content")
    ):
        return "member", message["title"], message["content"]

    default_message = config.get("default_message") or {}
    if (
        default_message.get("mode") == "custom"
        and default_message.get("title")
        and default_message.get("content")
    ):
        return "server", default_message["title"], default_message["content"]
    return (
        "keiko",
        birthday_default_text("title", locale),
        birthday_default_text("description", locale),
    )


def resolve_message(
    item: Mapping[str, Any], config: Mapping[str, Any], locale: str
) -> tuple[str, str]:
    """The title and the content that celebrate the member."""
    _source, title, content = message_source(item, config, locale)
    return title, content


def format_month_count(value: tuple[int, int] | None, locale: str) -> str:
    """A month and how many birthdays fall in it: `May (3)`."""
    if not value:
        return "-"

    label = get_month_label(int(value[0]), locale) or str(value[0])
    return f"{label} ({value[1]})"


def format_mm_dd_count(value: tuple[str, int] | None, locale: str) -> str:
    """A date and how many birthdays fall on it: `May 12 (2)`."""
    if not value:
        return "-"

    return f"{format_mm_dd_label(value[0], locale)} ({value[1]})"


def resolve_image(item: Mapping[str, Any]) -> str:
    """The member's custom image, or Keiko's."""
    image = item.get("image") or {}
    if image.get("mode") == "custom" and image.get("url"):
        return str(image["url"])
    return KeikoIcons.IMAGE_03


def build_celebration_embed(
    item: Mapping[str, Any],
    member: discord.abc.User,
    guild: discord.Guild,
    config: Mapping[str, Any],
    locale: str,
) -> discord.Embed:
    """The embed that celebrates the member's birthday in the server."""
    title, content = resolve_message(item, config, locale)
    mm_dd = item["date"]
    embed = announcement_embed(
        title=render_birthday_message(
            title, member.display_name, guild.name, mm_dd, locale
        ),
        message=render_birthday_message(
            content, member.mention, guild.name, mm_dd, locale
        ),
        image=resolve_image(item),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    return embed


async def send_birthday_preview(
    interaction: discord.Interaction, responses: list[dict[str, Any]]
) -> None:
    """The celebration as it will arrive: this member's message, or the default."""
    guild = interaction.guild
    if guild is None:
        return

    values = values_of(responses)
    member = interaction.user
    user_id = values.get("user")
    if user_id:
        member = await members.find(guild, user_id) or member

    saved_config = await birthday_config(str(guild.id)) or {}
    locale = parse_locale(str(interaction.locale))
    embed = build_celebration_embed(
        _previewed_birthday(values),
        member,
        guild,
        _previewed_config(values, saved_config),
        locale,
    )
    await MessagePreviewView([embed], locale).send(interaction)


def _previewed_config(
    values: Mapping[str, Any], saved_config: Mapping[str, Any]
) -> Mapping[str, Any]:
    if not values.get("default_message_mode"):
        return saved_config

    return {
        "default_message": {
            "mode": values.get("default_message_mode"),
            "title": values.get("default_message_title"),
            "content": values.get("default_message_content"),
        }
    }


def _previewed_birthday(values: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "date": values.get("date") or date.today().strftime("%m-%d"),
        "message": {
            "mode": values.get("use_custom_message") or "default",
            "title": values.get("custom_message_title"),
            "content": values.get("custom_message_content"),
        },
        "image": {
            "mode": values.get("use_custom_image") or "default",
            "url": values.get("custom_image"),
        },
    }


async def birthday_stats_lines(
    guild_id: str, locale: str, today: date | None = None
) -> list[str]:
    """The birthdays' own figures for their statistics, counted from `today`."""
    stats = await get_birthday_stats(guild_id, today)
    figures = [
        Figure("total", "🎂", stats.total),
        Figure("this_month", "📅", stats.this_month),
        Figure("top", "🏆", format_month_count(stats.busiest_month, locale)),
        Figure("quietest", "📉", format_month_count(stats.quietest_month, locale)),
        Figure("most_common", "⭐", format_mm_dd_count(stats.most_common_date, locale)),
    ]
    return figure_lines("reminders_birthday", figures, locale)


async def setup_birthdays(
    guild_id: str,
    channel_id: str,
    mention: str,
    timezone_value: str | None = None,
    notification_time: str | None = None,
    default_message: MessageChoice | None = None,
    updated_by: str | None = None,
    mention_roles: Sequence[str] = (),
) -> Mapping[str, Any]:
    """Save the birthday configuration in the server's zone, a new zone chosen here
    becoming the server's for new reminders; a new hour moves every reminder to it."""
    previous_config = await birthday_config(guild_id) or {}
    previous_zone = await _server_zone(guild_id)
    zone = timezone_value or previous_zone
    config = await save_birthday_config(
        guild_id,
        channel_id,
        mention,
        notification_time,
        default_message,
        updated_by,
        mention_roles,
    )

    if zone != previous_zone:
        await save_server_setting(
            guild_id, commands_constants.SERVER_TIMEZONE_KEY, zone
        )
    if previous_config.get("notification_time") != config.get("notification_time"):
        await reschedule_birthdays(guild_id, zone, config.get("notification_time"))
    return config


async def save_setup_form(
    guild_id: str,
    responses: list[dict[str, Any]],
    updated_by: str | None = None,
    display_names: Mapping[str, str | None] | None = None,
) -> list[Mapping[str, Any]]:
    """Save the setup form's configuration and each birthday it lists; those saved."""
    values = values_of(responses)
    mention = mention_of(values.get("mention"), values.get("mention_roles"))
    await setup_birthdays(
        guild_id,
        str(values.get("channel")),
        mention.kind,
        values.get("timezone"),
        values.get("notification_time"),
        _default_message_from_responses(responses),
        updated_by=updated_by,
        mention_roles=mention.roles,
    )

    saved_items = []
    for item in _form_birthday_items(values):
        user_id = str(item_values(item).get("user") or "")
        saved_item = await save_form_birthday_item(
            guild_id, item, (display_names or {}).get(user_id)
        )
        if saved_item:
            saved_items.append(saved_item)
    return saved_items


def _default_message_from_responses(responses: list[dict[str, Any]]) -> MessageChoice:
    values = values_of(responses)
    return _default_message_from_values(
        values.get("default_message_mode"),
        values.get("default_message_title"),
        values.get("default_message_content"),
    )


async def save_form_birthday_item(
    guild_id: str, item: Mapping[str, Any], display_name: str | None = None
) -> Mapping[str, Any] | None:
    """Save one birthday a form answered; nothing without a member or a real date."""
    birthday = _parse_form_birthday_item(item)
    if not birthday:
        return None

    return await upsert_birthday(
        guild_id,
        birthday.user_id,
        birthday.mm_dd,
        message=birthday.message,
        image=birthday.image,
        display_name=display_name,
    )


def form_birthday_users(responses: list[dict[str, Any]]) -> list[str]:
    """The members the birthdays of a setup form are for."""
    users = [
        item_values(item).get("user")
        for item in _form_birthday_items(values_of(responses))
    ]
    return [str(user) for user in users if user]


def _form_birthday_items(values: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items = values.get("reminders_birthday") or []
    return [items] if isinstance(items, dict) else list(items)


def _parse_form_birthday_item(item: Mapping[str, Any]) -> FormBirthday | None:
    values = item_values(item)
    user_id = values.get("user")
    mm_dd = values.get("date")
    if not user_id or not mm_dd or not is_valid_mm_dd(str(mm_dd)):
        return None

    custom_message = values.get("use_custom_message") == "custom"
    custom_image = values.get("use_custom_image") == "custom"
    return FormBirthday(
        user_id=str(user_id),
        mm_dd=str(mm_dd),
        message={
            "mode": "custom" if custom_message else "default",
            "title": values.get("custom_message_title") if custom_message else None,
            "content": values.get("custom_message_content") if custom_message else None,
        },
        image={
            "mode": "custom" if custom_image else "default",
            "url": values.get("custom_image") if custom_image else None,
        },
    )


async def disable_birthdays(guild_id: str) -> None:
    """Forget every birthday, reminder and the configuration of the guild."""
    dates = {item["date"] for item in await find_birthdays_by_guild(guild_id)}
    await data().guild_members.unset_option_in_guild(guild_id, "reminders_birthday")
    await data().guild_cogs.delete_cog_by_guild_id(guild_id, "reminders_birthday")
    for mm_dd in sorted(dates):
        await release_birthday_date(guild_id, mm_dd)


def to_summary_composition(item: Mapping[str, Any]) -> dict[str, CompositionField]:
    """A saved birthday as one row of the form's composition."""
    message = item.get("message") or {}
    image = item.get("image") or {}
    return {
        "type": {
            "value": "reminders_birthday",
            "title": "Type",
            "hidden": True,
        },
        "user": {"value": str(item.get("user_id")), "title": "Member", "style": "user"},
        "date": {"value": item.get("date"), "title": "Birthday", "style": "mm_dd"},
        "use_custom_message": {
            "value": message.get("mode", "default"),
            "title": "Message",
            "style": "boolean-mode",
            "hidden": message.get("mode", "default") != "custom",
        },
        "custom_message_title": {
            "value": message.get("title"),
            "title": "Title",
            "hidden": True,
        },
        "custom_message_content": {
            "value": message.get("content"),
            "title": "Content",
            "hidden": True,
        },
        "use_custom_image": {
            "value": image.get("mode", "default"),
            "title": "Custom Birthday Image",
            "style": "boolean-mode",
            "hidden": image.get("mode", "default") != "custom",
        },
        "custom_image": {
            "value": image.get("url"),
            "title": "Custom Birthday Image",
            "hidden": True,
        },
        "self_edit_count": {
            "value": item.get("self_edit_count", 0),
            "title": "Self edit count",
            "hidden": True,
        },
    }


class BirthdayFeature(GenericCogFeature):
    """Birthdays live in `guild.members`; the config in its cog document."""

    def __init__(self, today: Callable[[], date] = utc_today) -> None:
        super().__init__("reminders_birthday")
        self.today = today

    async def config_states(self) -> Sequence[Mapping[str, Any]]:
        """Every guild's birthday configuration keyed as the form names it, an unset
        value left unset."""
        return [
            await birthday_manager_cog_data(config["guild_id"], apply_defaults=False)
            for config in await data().guild_cogs.find_all_cogs(self.key)
            if config.get("guild_id")
        ]

    async def open(self, context: OpenContext) -> Opened:
        """The synthetic document the panel reads, paused or not, or nothing saved."""
        guild_id = context.guild_id
        config = await birthday_config(guild_id)
        if not config:
            return Opened()

        enabled = is_feature_on(config)
        document = await birthday_manager_cog_data(guild_id)
        today = self.today()
        rows = await birthday_settings_rows(guild_id, context.locale, today)
        pages = await birthday_month_pages(guild_id, context.locale, today)
        found = await birthday_found_members(guild_id, context.locale)
        return Opened(
            document=document,
            panel=PanelExtras(
                rows=tuple(rows), enabled=enabled, pages=tuple(pages), found=found
            ),
        )

    def asides(self) -> Mapping[str, AsideAction]:
        """The statistics and the preview of the celebration."""

        async def preview(interaction: Any, responses: Any) -> None:
            await send_birthday_preview(interaction, list(responses))

        return {
            **super().asides(),
            "preview": AsideAction(
                preview, defer=True, cooldown=view_constants.ACTION_COOLDOWN_SECONDS
            ),
        }

    async def stats_lines(self, guild_id: str, locale: str) -> Sequence[str]:
        """How many birthdays, this month's, and the busiest and quietest dates."""
        return await birthday_stats_lines(guild_id, locale, self.today())

    async def commit_setup(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """The config, the first birthday and its reminder; the feature is on."""
        responses = self.responses_for_preview(payload["answers"], context.locale)
        names = {
            user_id: await members.display_name(context.guild, user_id)
            for user_id in form_birthday_users(list(responses))
        }
        await save_setup_form(context.guild_id, responses, context.user_id, names)
        await set_feature_enabled(context.guild_id, self.key, True, context.user_id)
        return CommitResult(("reminders_birthday", "birthdays"))

    async def commit_edit(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Edited settings land on the config document, while it is still saved."""
        await self.saved_document(context)
        changes = self.to_document(payload["answers"], context.locale)
        await save_birthday_config_changes(context.guild_id, changes, context.user_id)
        return CommitResult(("reminders_birthday",))

    async def commit_edit_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """An edited member keeps its own birthday document, while the feature is
        still saved."""
        await self.saved_document(context)
        item = self.item_document(payload["answers"], context.locale)
        name = await members.display_name(
            context.guild, str(item_values(item).get("user") or "")
        )
        await save_form_birthday_item(context.guild_id, item, name)
        return CommitResult(("birthdays",))

    async def commit_add_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """A new member's birthday, refused when the member is already listed or the
        feature is no longer saved."""
        await self.saved_document(context)
        item = self.item_document(payload["answers"], context.locale)
        user_id = str(item_values(item).get("user") or "")
        existing = await find_birthday(context.guild_id, user_id)

        if existing:
            raise DuplicateItem(user_id)
        name = await members.display_name(context.guild, user_id)
        await save_form_birthday_item(context.guild_id, item, name)
        return CommitResult(("birthdays",))

    async def commit_remove_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Every chosen member's birthday goes while all are saved, or none does."""
        await self.saved_document(context)
        user_ids = [
            str(item_values(item).get("user") or "") for item in payload["items"]
        ]
        for user_id in user_ids:
            saved = await find_birthday(context.guild_id, user_id)
            if not user_id or saved is None:
                raise ListChanged(self.key)

        for user_id in user_ids:
            await remove_birthday(context.guild_id, user_id)
        return CommitResult(("birthdays",))

    async def commit_disable(
        self, _payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Every birthday, reminder and the config go while it is saved; the feature
        is off."""
        await self.saved_document(context)
        await disable_birthdays(context.guild_id)
        await to_thread(remove_cog_cache_by_guild, context.guild_id, self.key)
        return CommitResult(("reminders_birthday", "birthdays"))


FEATURE = BirthdayFeature()
