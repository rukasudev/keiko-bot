import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import discord

from collections import Counter

from app import logger
from app.components.embed import base_embed
from app.constants import (
    BLOCK_LINKS_LEGACY_LABEL_TO_DOMAIN,
    BLOCK_LINKS_QUICK_PICK_DOMAINS,
)
from app.constants import Commands as constants
from app.constants import KeikoIcons
from app.constants import LogTypes as logconstants
from app.constants import Style
from app.constants import ViewConstants as view_constants
from app.data import block_links as blocked_links_data
from app.exceptions import ErrorContext
from app.settings import open_feature
from app.services import analytics, cache

from .utils import (
    ParsedLink,
    check_two_lists_intersection,
    ensure_list,
    format_discord_timestamp,
    get_message_links,
    list_roles_id,
    ml,
    parse_form_yaml_to_dict,
    parse_link,
    parse_locale,
)


@dataclass(frozen=True)
class MessageSubject:
    """The Discord-aware boundary of the evaluation."""
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
    """`blocked_links` is what the rules reject; `would_block` also requires
    every gate to have passed."""
    mode: str
    gates: Tuple[GateOutcome, ...]
    links: Tuple[LinkVerdict, ...]
    blocked_links: Tuple[str, ...]
    would_block: bool
    answer: str = ""

    def gate(self, key: str) -> Optional[GateOutcome]:
        return next((gate for gate in self.gates if gate.key == key), None)


def matches_domain(link: ParsedLink, domain: str) -> bool:
    """The domain itself, a subdomain, or a quick-pick alias host."""
    candidates = [domain] + BLOCK_LINKS_QUICK_PICK_DOMAINS.get(domain, [])
    return any(
        link.host == candidate or link.host.endswith(f".{candidate}")
        for candidate in candidates
    )


def matches_exact(link: ParsedLink, stored: ParsedLink) -> bool:
    """Same host and path; the stored query params are a subset of the link's."""
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
        if match != "exact":
            match = "domain"
        rules.append({"link": link_value, "match": match})
    return rules


def _first_matching_rule(
    link: ParsedLink, rules: List[Dict[str, str]]
) -> Optional[Dict[str, str]]:
    """The custom entry that decides this link, or None."""
    for rule in rules:
        stored = parse_link(rule["link"])
        if rule["match"] == "exact":
            if matches_exact(link, stored):
                return rule
        elif matches_domain(link, stored.host):
            return rule
    return None


def link_verdicts(links: List[str], config: Dict[str, Any]) -> List["LinkVerdict"]:
    """Per-link decision under this normalized config, with the deciding rule."""
    mode = config.get(constants.BLOCK_LINKS_MODE_KEY) or "block_all"
    rules = _custom_entry_rules(config)
    domains = ensure_list(
        (config.get(constants.BLOCK_LINKS_ALLOWED_LINKS_KEY) or {}).get("values")
    )

    verdicts = []
    for raw in links:
        link = parse_link(raw)
        rule = _first_matching_rule(link, rules)

        if mode == "allow_all":
            if rule:
                verdicts.append(LinkVerdict(
                    raw=raw, host=link.host, blocked=True,
                    reason="blocked-by-custom",
                    rule=rule["link"], match=rule["match"],
                ))
            else:
                verdicts.append(LinkVerdict(
                    raw=raw, host=link.host, blocked=False,
                    reason="allowed-by-default",
                ))
            continue

        domain = next((d for d in domains if matches_domain(link, d)), None)
        if domain:
            verdicts.append(LinkVerdict(
                raw=raw, host=link.host, blocked=False,
                reason="allowed-by-popular",
                rule=domain, match="domain",
            ))
        elif rule:
            verdicts.append(LinkVerdict(
                raw=raw, host=link.host, blocked=False,
                reason="allowed-by-custom",
                rule=rule["link"], match=rule["match"],
            ))
        else:
            verdicts.append(LinkVerdict(
                raw=raw, host=link.host, blocked=True,
                reason="blocked-no-rule",
            ))
    return verdicts


def find_blocked_links(links: List[str], config: Dict[str, Any]) -> List[str]:
    """The extracted links this normalized config blocks."""
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
    """Read-time translation of legacy documents, used by enforcement and the
    manager alike. Never mutates the stored document."""
    config = dict(cogs or {})

    if not config.get(constants.BLOCK_LINKS_MODE_KEY):
        config[constants.BLOCK_LINKS_MODE_KEY] = "block_all"
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
    # Setup-only gate; keeping it would hide entries added through the manager.
    config.pop(constants.BLOCK_LINKS_ADD_CUSTOM_KEY, None)
    return config


def evaluate_message(
    subject: MessageSubject, cogs: Optional[Dict[str, Any]], *, full: bool = False
) -> BlockLinksEvaluation:
    """The single decision path of this command: enforcement (`full=False`)
    stops at the first failing gate, the diagnostic (`full=True`) walks all
    of them."""
    config = normalize_block_links_config(cogs)
    mode = config.get(constants.BLOCK_LINKS_MODE_KEY) or "block_all"
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
        reason = "not-configured"
    elif not cogs.get(constants.ENABLED_KEY, True):
        reason = "paused"
    else:
        reason = "active"
    active = reason == "active"
    gates.append(GateOutcome("feature", active, reason))
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
        "role",
        not role_exempt,
        "role-exempt" if role_exempt
        else "role-not-exempt",
        matched_roles,
    ))
    if role_exempt and not full:
        return finish()

    channel_exempt = subject.channel_id in config[
        constants.BLOCK_LINKS_ALLOWED_CHATS_KEY
    ]["values"]
    gates.append(GateOutcome(
        "channel",
        not channel_exempt,
        "channel-exempt" if channel_exempt
        else "channel-not-exempt",
        (subject.channel_id,),
    ))
    if channel_exempt and not full:
        return finish()

    message_links = get_message_links(subject.content)
    gates.append(GateOutcome(
        "links",
        bool(message_links),
        "has-links" if message_links
        else "no-links",
        tuple(message_links),
    ))
    if not message_links:
        return finish()

    verdicts = link_verdicts(message_links, config)
    blocked = [verdict.raw for verdict in verdicts if verdict.blocked]
    gates.append(GateOutcome(
        "rules",
        bool(blocked),
        "some-blocked" if blocked
        else "all-allowed",
        tuple(blocked),
    ))
    return finish()


async def check_message(guild_id: str, message: discord.Message) -> None:
    """Command service to check whether a message carries a blocked link."""
    # Every message in every guild reaches this line, and a cache miss reads Mongo.
    cogs = await asyncio.to_thread(
        cache.get_cog_data_or_populate, guild_id, constants.BLOCK_LINKS_KEY
    )

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
    """Store what was blocked (including failed deletes). Never raises."""
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
                constants.REDIS_BLOCK_LINKS_COUNTER_TOTAL.format(guild_id=guild_id)
            )
            cache.increment_redis_key(
                constants.REDIS_BLOCK_LINKS_COUNTER_HOST.format(
                    guild_id=guild_id, value=verdict.host
                )
            )
            cache.increment_redis_key(
                constants.REDIS_BLOCK_LINKS_COUNTER_USER.format(
                    guild_id=guild_id, value=getattr(author, "id", "")
                )
            )

        for verdict in blocked:
            analytics.emit(
                "feature.action_performed",
                guild_id=guild_id,
                feature=constants.BLOCK_LINKS_KEY,
                mode=evaluation.mode,
                reason=verdict.reason,
                deleted=deleted,
            )

        if blocked and not deleted:
            analytics.emit(
                "value.blocked_by_permission",
                guild_id=guild_id,
                feature=constants.BLOCK_LINKS_KEY,
                error_type="Forbidden",
            )
    except Exception as e:
        logger.error(
            f"Failed to record blocked links: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )


async def check_edited_message(bot, payload) -> None:
    """Re-run the check when a message is edited after being sent."""
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


def _bm(key: str, locale: str) -> str:
    return ml(f"commands.commands.commons.block-links-manager.{key}", locale=locale)


def _bc(key: str, locale: str) -> str:
    return ml(f"commands.commands.block-links-check.{key}", locale=locale)


def _mode_label(mode: str, locale: str) -> str:
    """The mode label, read from the same YAML that renders the picker."""
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
        "allowed-by-custom",
        "blocked-by-custom",
    ):
        return f"{verdict.reason}-{verdict.match or 'domain'}"
    return verdict.reason


def parse_evaluation_to_fields(
    evaluation: BlockLinksEvaluation, locale: str
) -> List[Dict[str, str]]:
    """The diagnostic as three items: feature on, exemptions, per-link fate."""
    feature = evaluation.gate("feature")
    feature_text = _bc(f"fields.feature.{feature.reason}", locale).replace(
        "$mode", _mode_label(evaluation.mode, locale)
    )

    exemptions = []
    role = evaluation.gate("role")
    if role:
        exemptions.append(
            _bc(f"fields.exemptions.{role.reason}", locale).replace(
                "$roles", ", ".join(f"<@&{value}>" for value in role.detail)
            )
        )
    channel = evaluation.gate("channel")
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
    analytics.emit(
        "feature.tested",
        guild_id=interaction.guild_id,
        user_id=interaction.user.id,
        feature=constants.BLOCK_LINKS_KEY,
        source="context_menu",
        surface="diagnostic",
    )
    locale = parse_locale(interaction.locale)
    cogs = await asyncio.to_thread(
        cache.get_cog_data_or_populate,
        str(interaction.guild_id),
        constants.BLOCK_LINKS_KEY,
        True,
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
    """Records as the {field name: field value} dict PaginationView renders."""
    data = {}
    for index, record in enumerate(records, start=1):
        icon = "⚠️" if record.get("deleted") is False else "🚫"
        title = f"{index}. {icon} {record.get('host') or '-'}"

        lines = [f"`{record.get('link') or '-'}`"]
        lines.append(
            _bm("blocked-list.record.line", locale)
            .replace("$user", f"<@{record.get('user_id')}>")
            .replace("$channel", f"<#{record.get('channel_id')}>")
            .replace("$date", format_discord_timestamp(record.get("created_at")))
        )
        if record.get("rule"):
            lines.append(
                _bm("blocked-list.record.rule", locale).replace("$rule", record["rule"])
            )
        if record.get("deleted") is False:
            lines.append(_bm("blocked-list.record.not-deleted", locale))

        data[title] = "\n".join(lines)
    return data


def get_blocked_links_stats(guild_id: str) -> Dict[str, Any]:
    """All-time numbers from the Redis counters, recent ones from the records."""
    records = blocked_links_data.find_blocked_links_by_guild(guild_id)

    host_counters = cache.get_redis_counters_by_prefix(
        constants.REDIS_BLOCK_LINKS_COUNTER_HOST.format(guild_id=guild_id, value="")
    ) or Counter(record.get("host") for record in records if record.get("host"))
    user_counters = cache.get_redis_counters_by_prefix(
        constants.REDIS_BLOCK_LINKS_COUNTER_USER.format(guild_id=guild_id, value="")
    ) or Counter(record.get("user_id") for record in records if record.get("user_id"))

    top_hosts = Counter(host_counters).most_common(3)
    top_users = Counter(user_counters).most_common(1)

    return {
        "total": cache.get_redis_counter(
            constants.REDIS_BLOCK_LINKS_COUNTER_TOTAL.format(guild_id=guild_id)
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

    embed = base_embed(
        _bm("stats.embed.title", locale),
        description,
        footer=ml("commands.commands.commons.embed.footer", locale=locale),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


async def send_blocked_links_message(
    interaction: discord.Interaction, user_id: Optional[str] = None
) -> None:
    from app.views.records import RecordsBrowser

    locale = parse_locale(interaction.locale)
    browser = RecordsBrowser(
        fetch=lambda i, uid: get_blocked_link_records(str(i.guild_id), user_id=uid),
        to_fields=lambda records, i: parse_blocked_link_records(
            records, parse_locale(i.locale)
        ),
        title=_bm("blocked-list.embed.title", locale),
        description=_bm("blocked-list.embed.description", locale),
        empty_description=_bm("blocked-list.embed.empty", locale),
        filter_namespace="commands.commands.commons.block-links-manager.blocked-list.filter",
    )
    await browser.send(interaction, user_id=user_id)


async def manager(interaction: discord.Interaction, guild_id: str) -> None:
    """The slash command: the setup form, or the manager of what is saved."""
    await open_feature(interaction, constants.BLOCK_LINKS_KEY)
