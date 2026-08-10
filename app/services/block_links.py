from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlparse

import discord

from collections import Counter

from app import logger
from app.components.buttons import AdditionalButton
from app.constants import (
    BLOCK_LINKS_LEGACY_LABEL_TO_DOMAIN,
    BLOCK_LINKS_QUICK_PICK_DOMAINS,
)
from app.constants import Commands as constants
from app.constants import KeikoIcons
from app.constants import LogTypes as logconstants
from app.constants import Style
from app.data import blocked_links as blocked_links_data
from app.exceptions import ErrorContext
from app.services import cache
from app.services.moderations import (
    send_command_form_message,
    send_command_manager_message,
)

from .utils import (
    check_two_lists_intersection,
    ensure_list,
    get_message_links,
    list_roles_id,
    ml,
    parse_form_yaml_to_dict,
    parse_locale,
)


@dataclass(frozen=True)
class ParsedLink:
    host: str
    path: str
    query: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MessageSubject:
    """The only Discord-aware boundary of the evaluation: everything past this
    point is primitives, so the whole decision is unit-testable offline."""
    author_role_ids: Tuple[str, ...] = ()
    channel_id: str = ""
    content: str = ""
    author_is_bot: bool = False

    @classmethod
    def from_message(cls, message) -> "MessageSubject":
        author = getattr(message, "author", None)
        channel = getattr(message, "channel", None)
        return cls(
            author_role_ids=tuple(list_roles_id(getattr(author, "roles", None) or [])),
            channel_id=str(getattr(channel, "id", "")),
            content=getattr(message, "content", "") or "",
            author_is_bot=bool(getattr(author, "bot", False)),
        )


@dataclass(frozen=True)
class GateOutcome:
    key: str
    passed: bool          # True = enforcement continues past this gate
    reason: str
    detail: Tuple[str, ...] = ()


@dataclass(frozen=True)
class LinkVerdict:
    raw: str
    host: str
    blocked: bool
    reason: str
    rule: Optional[str] = None
    match: Optional[str] = None


@dataclass(frozen=True)
class BlockLinksEvaluation:
    """`blocked_links` is what the link RULES reject; `would_block` is what
    actually happens, which also requires every gate to have passed. They
    differ exactly when an exemption spares a message that breaks a rule,
    which is the most useful thing the diagnostic can tell a moderator."""
    mode: str
    gates: Tuple[GateOutcome, ...]
    links: Tuple[LinkVerdict, ...]
    blocked_links: Tuple[str, ...]
    would_block: bool
    answer: str = ""

    def gate(self, key: str) -> Optional[GateOutcome]:
        return next((gate for gate in self.gates if gate.key == key), None)


def parse_link(text: str) -> ParsedLink:
    """Normalize a link or domain: optional scheme, lowercase host, single
    leading www. stripped, single trailing slash stripped, fragment dropped."""
    text = str(text).strip()
    if "://" not in text:
        text = f"http://{text}"
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[len("www."):]
    path = parsed.path or ""
    if path.endswith("/"):
        path = path[:-1]
    return ParsedLink(host=host, path=path, query=dict(parse_qsl(parsed.query)))


def matches_domain(link: ParsedLink, domain: str) -> bool:
    """The link's host is the domain, one of its subdomains, or one of the
    quick-pick alias hosts (youtu.be for youtube.com, x.com for twitter.com...)."""
    candidates = [domain] + BLOCK_LINKS_QUICK_PICK_DOMAINS.get(domain, [])
    return any(
        link.host == candidate or link.host.endswith(f".{candidate}")
        for candidate in candidates
    )


def matches_exact(link: ParsedLink, stored: ParsedLink) -> bool:
    """Same host and path; the stored query params must be a subset of the
    link's (so tracking params on the message side never defeat a match,
    but a different ?v= does)."""
    if link.host != stored.host or link.path != stored.path:
        return False
    return all(link.query.get(key) == value for key, value in stored.query.items())


def _custom_entry_rules(config: Dict[str, Any]) -> List[Dict[str, str]]:
    rules = []
    for entry in ensure_list(
        (config.get(constants.BLOCK_LINKS_CUSTOM_LINKS_KEY) or {}).get("values")
    ):
        if not isinstance(entry, dict):
            continue
        link_value = (entry.get(constants.BLOCK_LINKS_LINK_KEY) or {}).get("value")
        if not link_value:
            continue
        match_field = entry.get(constants.BLOCK_LINKS_MATCH_TYPE_KEY) or {}
        match = match_field.get("_raw_value") or match_field.get("value")
        if match != constants.BLOCK_LINKS_MATCH_EXACT:
            match = constants.BLOCK_LINKS_MATCH_DOMAIN
        rules.append({"link": link_value, "match": match})
    return rules


def _first_matching_rule(
    link: ParsedLink, rules: List[Dict[str, str]]
) -> Optional[Dict[str, str]]:
    """The custom entry that decides this link, or None. Returning the rule
    (instead of a bare bool) is what lets both the recorded event and the
    diagnostic name WHY a link was allowed or blocked."""
    for rule in rules:
        stored = parse_link(rule["link"])
        if rule["match"] == constants.BLOCK_LINKS_MATCH_EXACT:
            if matches_exact(link, stored):
                return rule
        elif matches_domain(link, stored.host):
            return rule
    return None


def _matches_entries(link: ParsedLink, rules: List[Dict[str, str]]) -> bool:
    return _first_matching_rule(link, rules) is not None


def link_verdicts(links: List[str], config: Dict[str, Any]) -> List["LinkVerdict"]:
    """Per-link decision under this (normalized) config, with the rule that
    decided it. The single place where the block/allow call is made."""
    mode = config.get(constants.BLOCK_LINKS_MODE_KEY) or constants.BLOCK_LINKS_MODE_BLOCK_ALL
    rules = _custom_entry_rules(config)
    domains = ensure_list(
        (config.get(constants.BLOCK_LINKS_ALLOWED_LINKS_KEY) or {}).get("values")
    )

    verdicts = []
    for raw in links:
        link = parse_link(raw)
        rule = _first_matching_rule(link, rules)

        if mode == constants.BLOCK_LINKS_MODE_ALLOW_ALL:
            if rule:
                verdicts.append(LinkVerdict(
                    raw=raw, host=link.host, blocked=True,
                    reason=constants.BLOCK_LINKS_REASON_BLOCKED_BY_CUSTOM,
                    rule=rule["link"], match=rule["match"],
                ))
            else:
                verdicts.append(LinkVerdict(
                    raw=raw, host=link.host, blocked=False,
                    reason=constants.BLOCK_LINKS_REASON_ALLOWED_BY_DEFAULT,
                ))
            continue

        domain = next((d for d in domains if matches_domain(link, d)), None)
        if domain:
            verdicts.append(LinkVerdict(
                raw=raw, host=link.host, blocked=False,
                reason=constants.BLOCK_LINKS_REASON_ALLOWED_BY_POPULAR,
                rule=domain, match=constants.BLOCK_LINKS_MATCH_DOMAIN,
            ))
        elif rule:
            verdicts.append(LinkVerdict(
                raw=raw, host=link.host, blocked=False,
                reason=constants.BLOCK_LINKS_REASON_ALLOWED_BY_CUSTOM,
                rule=rule["link"], match=rule["match"],
            ))
        else:
            verdicts.append(LinkVerdict(
                raw=raw, host=link.host, blocked=True,
                reason=constants.BLOCK_LINKS_REASON_BLOCKED_NO_RULE,
            ))
    return verdicts


def find_blocked_links(links: List[str], config: Dict[str, Any]) -> List[str]:
    """Which of the extracted links must be blocked under this (normalized)
    config. Never mutates the input."""
    return [verdict.raw for verdict in link_verdicts(links, config) if verdict.blocked]


def _envelope(config: Dict[str, Any], key: str, style: Optional[str]) -> Dict[str, Any]:
    current = config.get(key)
    if isinstance(current, dict):
        return {**current, "values": ensure_list(current.get("values"))}
    envelope = {"values": ensure_list(current)}
    if style:
        envelope["style"] = style
    return envelope


def normalize_block_links_config(cogs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Read-time translation used by BOTH enforcement and the manager.

    Legacy documents (saved before the two-mode redesign) carry option
    LABELS in allowed_links and no mode; they keep working forever through
    this function. Never mutates the stored document."""
    config = dict(cogs or {})

    if not config.get(constants.BLOCK_LINKS_MODE_KEY):
        config[constants.BLOCK_LINKS_MODE_KEY] = constants.BLOCK_LINKS_MODE_BLOCK_ALL
        legacy_value = config.get(constants.BLOCK_LINKS_ALLOWED_LINKS_KEY)
        if not isinstance(legacy_value, dict):
            domains = [
                BLOCK_LINKS_LEGACY_LABEL_TO_DOMAIN[label.lower()]
                for label in ensure_list(legacy_value)
                if isinstance(label, str)
                and label.lower() in BLOCK_LINKS_LEGACY_LABEL_TO_DOMAIN
            ]
            config[constants.BLOCK_LINKS_ALLOWED_LINKS_KEY] = {
                "style": "bullet",
                "values": domains,
            }

    config[constants.BLOCK_LINKS_ALLOWED_CHATS_KEY] = _envelope(
        config, constants.BLOCK_LINKS_ALLOWED_CHATS_KEY, "channel"
    )
    config[constants.BLOCK_LINKS_ALLOWED_ROLES_KEY] = _envelope(
        config, constants.BLOCK_LINKS_ALLOWED_ROLES_KEY, "role"
    )
    config[constants.BLOCK_LINKS_ALLOWED_LINKS_KEY] = _envelope(
        config, constants.BLOCK_LINKS_ALLOWED_LINKS_KEY, "bullet"
    )
    config[constants.BLOCK_LINKS_CUSTOM_LINKS_KEY] = _envelope(
        config, constants.BLOCK_LINKS_CUSTOM_LINKS_KEY, "composition"
    )
    # Setup-only gate: if kept, its condition would hide entries added later
    # through the manager Add button.
    config.pop(constants.BLOCK_LINKS_ADD_CUSTOM_KEY, None)
    return config


def evaluate_message(
    subject: MessageSubject, cogs: Optional[Dict[str, Any]], *, full: bool = False
) -> BlockLinksEvaluation:
    """The single decision path of this command.

    Enforcement (`full=False`) stops at the first gate that fails, doing
    exactly the work it did when the gates were inline early returns. The
    diagnostic (`full=True`) walks every gate so it can tell the user which
    rules a message passed and which it did not. Both read the same gates in
    the same order, so an explanation can never disagree with what the bot
    actually does."""
    config = normalize_block_links_config(cogs)
    mode = config.get(constants.BLOCK_LINKS_MODE_KEY) or constants.BLOCK_LINKS_MODE_BLOCK_ALL
    answer = config.get(constants.BLOCK_LINKS_ANSWER_KEY) or ""

    gates: List[GateOutcome] = []
    verdicts: List[LinkVerdict] = []

    def finish() -> BlockLinksEvaluation:
        blocked = tuple(verdict.raw for verdict in verdicts if verdict.blocked)
        return BlockLinksEvaluation(
            mode=mode,
            gates=tuple(gates),
            links=tuple(verdicts),
            blocked_links=blocked,
            would_block=bool(blocked) and all(gate.passed for gate in gates),
            answer=answer,
        )

    if not cogs:
        reason = constants.BLOCK_LINKS_REASON_NOT_CONFIGURED
    elif not cogs.get(constants.ENABLED_KEY, True):
        reason = constants.BLOCK_LINKS_REASON_PAUSED
    else:
        reason = constants.BLOCK_LINKS_REASON_ACTIVE
    active = reason == constants.BLOCK_LINKS_REASON_ACTIVE
    gates.append(GateOutcome(constants.BLOCK_LINKS_GATE_FEATURE, active, reason))
    if not active and not full:
        return finish()

    exempt_roles = config[constants.BLOCK_LINKS_ALLOWED_ROLES_KEY]["values"]
    matched_roles = tuple(
        role_id for role_id in subject.author_role_ids if role_id in exempt_roles
    )
    role_exempt = check_two_lists_intersection(
        list(subject.author_role_ids), exempt_roles
    )
    gates.append(GateOutcome(
        constants.BLOCK_LINKS_GATE_ROLE,
        not role_exempt,
        constants.BLOCK_LINKS_REASON_ROLE_EXEMPT if role_exempt
        else constants.BLOCK_LINKS_REASON_ROLE_NOT_EXEMPT,
        matched_roles,
    ))
    if role_exempt and not full:
        return finish()

    channel_exempt = subject.channel_id in config[
        constants.BLOCK_LINKS_ALLOWED_CHATS_KEY
    ]["values"]
    gates.append(GateOutcome(
        constants.BLOCK_LINKS_GATE_CHANNEL,
        not channel_exempt,
        constants.BLOCK_LINKS_REASON_CHANNEL_EXEMPT if channel_exempt
        else constants.BLOCK_LINKS_REASON_CHANNEL_NOT_EXEMPT,
        (subject.channel_id,),
    ))
    if channel_exempt and not full:
        return finish()

    message_links = get_message_links(subject.content)
    gates.append(GateOutcome(
        constants.BLOCK_LINKS_GATE_LINKS,
        bool(message_links),
        constants.BLOCK_LINKS_REASON_HAS_LINKS if message_links
        else constants.BLOCK_LINKS_REASON_NO_LINKS,
        tuple(message_links),
    ))
    if not message_links:
        return finish()

    verdicts = link_verdicts(message_links, config)
    blocked = [verdict.raw for verdict in verdicts if verdict.blocked]
    gates.append(GateOutcome(
        constants.BLOCK_LINKS_GATE_RULES,
        bool(blocked),
        constants.BLOCK_LINKS_REASON_SOME_BLOCKED if blocked
        else constants.BLOCK_LINKS_REASON_ALL_ALLOWED,
        tuple(blocked),
    ))
    return finish()


async def check_message(guild_id: str, message: discord.Message) -> None:
    """Command service to check whether a message carries a blocked link."""
    cogs = cache.get_cog_data_or_populate(guild_id, constants.BLOCK_LINKS_KEY)

    evaluation = evaluate_message(MessageSubject.from_message(message), cogs)
    if not evaluation.would_block:
        return

    blocked_links = list(evaluation.blocked_links)

    context = ErrorContext.from_message(
        flow="block_links",
        message=message,
        blocked_links=blocked_links[:3],
    )

    answer = evaluation.answer.replace("{user}", message.author.mention)

    deleted = False
    try:
        await message.delete()
        deleted = True
        if answer:
            await message.channel.send(answer, delete_after=5)
    except Exception as e:
        logger.error(
            f"Failed to block link: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise
    finally:
        record_blocked_links(guild_id, message, evaluation, deleted=deleted)


def record_blocked_links(
    guild_id: str,
    message: discord.Message,
    evaluation: BlockLinksEvaluation,
    *,
    deleted: bool,
) -> None:
    """Store what I blocked, so the server can list it and see its stats.

    Recorded after the delete attempt and including failures: `deleted: False`
    means I matched a link but could not remove it (usually a missing Manage
    Messages permission), which is the most useful thing an owner can learn.

    Never raises: an audit write must not break moderation, nor turn a
    successful delete into an error path."""
    try:
        author = getattr(message, "author", None)
        channel = getattr(message, "channel", None)
        blocked = [verdict for verdict in evaluation.links if verdict.blocked]

        for verdict in blocked[:constants.BLOCK_LINKS_EVENTS_MAX_PER_MESSAGE]:
            blocked_links_data.insert_blocked_link({
                "guild_id": str(guild_id),
                "user_id": str(getattr(author, "id", "")),
                "channel_id": str(getattr(channel, "id", "")),
                "message_id": str(getattr(message, "id", "")),
                "link": verdict.raw[:100],
                "host": verdict.host,
                "mode": evaluation.mode,
                "reason": verdict.reason,
                "rule": verdict.rule,
                "match": verdict.match,
                "deleted": deleted,
            })
            cache.increment_redis_key(
                constants.BLOCK_LINKS_COUNTER_TOTAL.format(guild_id=guild_id)
            )
            cache.increment_redis_key(
                constants.BLOCK_LINKS_COUNTER_HOST.format(
                    guild_id=guild_id, value=verdict.host
                )
            )
            cache.increment_redis_key(
                constants.BLOCK_LINKS_COUNTER_USER.format(
                    guild_id=guild_id, value=getattr(author, "id", "")
                )
            )
    except Exception as e:
        logger.error(
            f"Failed to record blocked links: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )


async def check_edited_message(bot, payload) -> None:
    """Re-run the check when a message is edited after being sent.

    Editing a harmless message into a link was a free bypass while the bot
    only listened to on_message. The raw event is used (not on_message_edit)
    because it also fires for messages that left the client cache, and it
    fires exactly once per edit.

    Two cheap guards run before any HTTP call: Discord also emits an update
    when it attaches the link preview (no `content` in the payload), and an
    edit whose new text carries no link has nothing to re-evaluate."""
    if not getattr(payload, "guild_id", None):
        return

    content = (getattr(payload, "data", None) or {}).get("content")
    if not content or not get_message_links(content):
        return

    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except discord.HTTPException:
        return

    if message is None or message.author is None or message.author.bot:
        return

    await check_message(str(payload.guild_id), message)


MANAGER_NAMESPACE = "commands.commands.commons.block-links-manager"
CHECK_NAMESPACE = "commands.commands.block-links-check"


def _bm(key: str, locale: str) -> str:
    return ml(f"{MANAGER_NAMESPACE}.{key}", locale=locale)


def _bc(key: str, locale: str) -> str:
    return ml(f"{CHECK_NAMESPACE}.{key}", locale=locale)


def _mode_label(mode: str, locale: str) -> str:
    """The mode as the user picked it, read from the same YAML that renders
    the picker, so the diagnostic can never drift from the card."""
    for step in parse_form_yaml_to_dict(constants.BLOCK_LINKS_KEY):
        for section in step.get("sections", []) or []:
            if section.get("key") != constants.BLOCK_LINKS_MODE_KEY:
                continue
            for option in section.get("options", []) or []:
                if str(option.get("value")) == mode:
                    return (option.get("label") or {}).get(locale, mode)
    return mode


def _link_reason_key(verdict: LinkVerdict) -> str:
    if verdict.reason in (
        constants.BLOCK_LINKS_REASON_ALLOWED_BY_CUSTOM,
        constants.BLOCK_LINKS_REASON_BLOCKED_BY_CUSTOM,
    ):
        return f"{verdict.reason}-{verdict.match or constants.BLOCK_LINKS_MATCH_DOMAIN}"
    return verdict.reason


def parse_evaluation_to_fields(
    evaluation: BlockLinksEvaluation, locale: str
) -> List[Dict[str, str]]:
    """The diagnostic as exactly three items: is the feature on, is anyone
    exempt, and what happened to each link. Pure, so the wording is asserted
    without touching Discord."""
    feature = evaluation.gate(constants.BLOCK_LINKS_GATE_FEATURE)
    feature_text = _bc(f"fields.feature.{feature.reason}", locale).replace(
        "$mode", _mode_label(evaluation.mode, locale)
    )

    exemptions = []
    role = evaluation.gate(constants.BLOCK_LINKS_GATE_ROLE)
    if role:
        exemptions.append(
            _bc(f"fields.exemptions.{role.reason}", locale).replace(
                "$roles", ", ".join(f"<@&{value}>" for value in role.detail)
            )
        )
    channel = evaluation.gate(constants.BLOCK_LINKS_GATE_CHANNEL)
    if channel:
        exemptions.append(
            _bc(f"fields.exemptions.{channel.reason}", locale).replace(
                "$channel", f"<#{channel.detail[0]}>" if channel.detail else "-"
            )
        )

    limit = constants.BLOCK_LINKS_DIAGNOSTIC_MAX_LINKS
    link_lines = [
        _bc(f"fields.links.reasons.{_link_reason_key(verdict)}", locale)
        .replace("$link", verdict.raw[:100])
        .replace("$rule", verdict.rule or "")
        for verdict in evaluation.links[:limit]
    ]
    if not link_lines:
        link_lines = [_bc("fields.links.none", locale)]
    if len(evaluation.links) > limit:
        link_lines.append(
            _bc("fields.links.more", locale).replace(
                "$count", str(len(evaluation.links) - limit)
            )
        )

    return [
        {"title": _bc("fields.feature.title", locale), "value": feature_text},
        {
            "title": _bc("fields.exemptions.title", locale),
            "value": "\n".join(exemptions) or "-",
        },
        {"title": _bc("fields.links.title", locale), "value": "\n".join(link_lines)},
    ]


async def send_link_check_message(
    interaction: discord.Interaction, message: discord.Message
) -> None:
    """Message context menu: why a link of this message was (not) blocked."""
    locale = parse_locale(interaction.locale)
    cogs = cache.get_cog_data_or_populate(
        str(interaction.guild_id), constants.BLOCK_LINKS_KEY, manager=True
    )
    evaluation = evaluate_message(
        MessageSubject.from_message(message), cogs, full=True
    )

    title = _bc(
        "embed.title-blocked" if evaluation.would_block else "embed.title-allowed",
        locale,
    )
    embed = discord.Embed(title=title, color=int(Style.BACKGROUND_COLOR, base=16))
    for field in parse_evaluation_to_fields(evaluation, locale):
        embed.add_field(name=field["title"], value=field["value"], inline=False)
    embed.set_thumbnail(url=KeikoIcons.IMAGE_01)

    footer = ml("commands.commands.commons.embed.footer", locale=locale)
    if footer:
        embed.set_footer(text=f"• {footer}")

    await interaction.response.send_message(embed=embed, ephemeral=True)


def get_blocked_link_records(
    guild_id: str, user_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    return blocked_links_data.find_blocked_links_by_guild(
        guild_id, user_id=user_id, limit=constants.BLOCK_LINKS_EVENTS_READ_LIMIT
    )


def parse_blocked_link_records(
    records: List[Dict[str, Any]], locale: str
) -> Dict[str, str]:
    """Records as the {field name: field value} dict PaginationView renders.
    Field names carry the position so two blocks of the same website never
    collapse into one another."""
    data = {}
    for index, record in enumerate(records, start=1):
        icon = "⚠️" if record.get("deleted") is False else "🚫"
        title = f"{index}. {icon} {record.get('host') or '-'}"

        lines = [f"`{record.get('link') or '-'}`"]
        lines.append(
            _bm("blocked-list.record.line", locale)
            .replace("$user", f"<@{record.get('user_id')}>")
            .replace("$channel", f"<#{record.get('channel_id')}>")
            .replace("$date", _discord_timestamp(record.get("created_at")))
        )
        if record.get("rule"):
            lines.append(
                _bm("blocked-list.record.rule", locale).replace("$rule", record["rule"])
            )
        if record.get("deleted") is False:
            lines.append(_bm("blocked-list.record.not-deleted", locale))

        data[title] = "\n".join(lines)
    return data


def _discord_timestamp(created_at: Any) -> str:
    """`<t:unix:R>` renders as a relative time ("2 hours ago") translated by
    each reader's own Discord client, which a formatted UTC string never is."""
    if not hasattr(created_at, "timestamp"):
        return "-"
    return f"<t:{int(created_at.timestamp())}:R>"


def get_blocked_links_stats(guild_id: str) -> Dict[str, Any]:
    """All-time numbers come from the Redis counters, which outlive the 90-day
    retention of the records; the recent cut and the failure count come from
    the records themselves. The two windows are labeled apart in the copy."""
    records = blocked_links_data.find_blocked_links_by_guild(guild_id)

    host_counters = cache.get_redis_counters_by_prefix(
        constants.BLOCK_LINKS_COUNTER_HOST.format(guild_id=guild_id, value="")
    ) or Counter(record.get("host") for record in records if record.get("host"))
    user_counters = cache.get_redis_counters_by_prefix(
        constants.BLOCK_LINKS_COUNTER_USER.format(guild_id=guild_id, value="")
    ) or Counter(record.get("user_id") for record in records if record.get("user_id"))

    top_hosts = Counter(host_counters).most_common(3)
    top_users = Counter(user_counters).most_common(1)

    return {
        "total": cache.get_redis_counter(
            constants.BLOCK_LINKS_COUNTER_TOTAL.format(guild_id=guild_id)
        ) or len(records),
        "recent": len(records),
        "top_hosts": top_hosts,
        "top_user": top_users[0] if top_users else None,
        "not_deleted": sum(
            1 for record in records if record.get("deleted") is False
        ),
    }


async def send_blocked_links_stats_message(interaction: discord.Interaction) -> None:
    locale = parse_locale(interaction.locale)
    stats = get_blocked_links_stats(str(interaction.guild_id))

    if not stats["total"]:
        description = _bm("stats.empty", locale)
    else:
        top_hosts = ", ".join(
            f"`{host}` ({count})" for host, count in stats["top_hosts"]
        ) or "-"
        top_user = (
            f"<@{stats['top_user'][0]}> ({stats['top_user'][1]})"
            if stats["top_user"] else "-"
        )
        lines = [
            f"🚫 **{_bm('stats.fields.total', locale)}:** {stats['total']}",
            f"📅 **{_bm('stats.fields.recent', locale)}:** {stats['recent']}",
            f"🌐 **{_bm('stats.fields.top-websites', locale)}:** {top_hosts}",
            f"🙋 **{_bm('stats.fields.top-member', locale)}:** {top_user}",
        ]
        if stats["not_deleted"]:
            lines.append(
                f"⚠️ **{_bm('stats.fields.not-deleted', locale)}:** {stats['not_deleted']}"
            )
        description = "\n".join(lines)

    embed = discord.Embed(
        title=_bm("stats.embed.title", locale),
        description=description,
        color=int(Style.BACKGROUND_COLOR, base=16),
    )
    # Same picture as the blocked-links list: both screens are the same feature
    # seen from two angles.
    embed.set_thumbnail(url=KeikoIcons.IMAGE_02)
    footer = ml("commands.commands.commons.embed.footer", locale=locale)
    if footer:
        embed.set_footer(text=f"• {footer}")

    await interaction.followup.send(embed=embed, ephemeral=True)


def disable_block_links(interaction: discord.Interaction, cogs: Any = None) -> None:
    """Turning the feature off purges what I recorded: the user asked me to
    stop watching, so I stop keeping their history too."""
    blocked_links_data.delete_blocked_links_by_guild(str(interaction.guild_id))


def _manager_info(locale: str) -> str:
    """The step by step to reach the diagnostic app command, shown on the
    manager panel because a context menu is easy to never discover."""
    return _bm("info", locale)


def _manager_info_title(locale: str) -> str:
    return _bm("info-title", locale)


def _manager_buttons(locale: str) -> List[discord.ui.Button]:
    from app.views.blocked_links import send_blocked_links_message

    return [
        AdditionalButton(
            callback=send_blocked_links_message,
            label=_bm("blocked-list.button.label", locale),
            desc=_bm("blocked-list.button.desc", locale),
            emoji="🔎",
            style=discord.ButtonStyle.grey,
            own_response=True,
            cooldown=constants.VIEW_ACTION_COOLDOWN_SECONDS,
        ),
        AdditionalButton(
            callback=send_blocked_links_stats_message,
            label=ml("buttons.stats.label", locale=locale),
            desc=_bm("stats.button.desc", locale),
            emoji="📊",
            style=discord.ButtonStyle.grey,
            defer=True,
            cooldown=constants.VIEW_ACTION_COOLDOWN_SECONDS,
        ),
    ]


async def manager(interaction: discord.Interaction, guild_id: str) -> None:
    cogs = cache.get_cog_data_or_populate(
        guild_id, constants.BLOCK_LINKS_KEY, manager=True
    )

    if cogs is None:
        return await send_command_form_message(interaction, constants.BLOCK_LINKS_KEY)

    locale = parse_locale(interaction.locale)
    await send_command_manager_message(
        interaction,
        constants.BLOCK_LINKS_KEY,
        normalize_block_links_config(cogs),
        additional_info=_manager_info(locale),
        additional_buttons=_manager_buttons(locale),
        lifecycle_callbacks={constants.LIFECYCLE_DISABLE: disable_block_links},
        additional_info_title=_manager_info_title(locale),
    )
