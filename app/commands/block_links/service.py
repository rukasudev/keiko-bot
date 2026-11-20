"""Block links: the rule that deletes a link, its diagnostic and its records."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import discord

from app import logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants as view_constants
from app.core import analytics, cache, i18n, members
from app.core.commands import MessageEdited, MessageSent
from app.core.facts import Facts, facts
from app.core.feature_health import Figure, figure_lines
from app.core.threads import to_thread
from app.core.utils import (
    check_two_lists_intersection,
    ensure_list,
    fill,
    fill_placeholders,
    format_discord_timestamp,
    get_message_links,
    parse_locale,
)
from app.data import data
from app.exceptions import ErrorContext
from app.forms import document_values, list_values, open_feature
from app.forms.features.feature import (
    AsideAction,
    CommitContext,
    GenericCogFeature,
    OpenContext,
    Opened,
)
from app.forms.form.form_yaml import CardStep, registry
from app.forms.form.responses.links import ParsedLink, parse_link
from app.ui.embed import Response, response_embed
from app.ui.records import Records, RecordsBrowser


def command_facts(**values: Any) -> Facts:
    """The facts this command's code passes its copy, each under its address."""
    return facts("block_links", **values)


@dataclass(frozen=True)
class MessageSubject:
    """The Discord-aware boundary of the evaluation."""

    author_role_ids: Tuple[str, ...] = ()
    channel_id: str = ""
    content: str = ""
    author_is_bot: bool = False

    @classmethod
    def from_message(cls, message: discord.Message) -> "MessageSubject":
        """The parts of `message` the gates read."""
        author = getattr(message, "author", None)
        channel = getattr(message, "channel", None)
        roles = getattr(author, "roles", None) or []
        return cls(
            author_role_ids=tuple(str(role.id) for role in roles),
            channel_id=str(getattr(channel, "id", "")),
            content=getattr(message, "content", "") or "",
            author_is_bot=bool(getattr(author, "bot", False)),
        )


@dataclass(frozen=True)
class GateOutcome:
    """One gate of the evaluation, `passed` when enforcement goes on past it."""

    key: str
    passed: bool
    reason: str
    detail: Tuple[str, ...] = ()


@dataclass(frozen=True)
class LinkVerdict:
    """The fate of one link of the message and the rule that decided it."""

    raw: str
    host: str
    blocked: bool
    reason: str
    rule: Optional[str] = None
    match: Optional[str] = None


@dataclass(frozen=True)
class BlockLinksEvaluation:
    """The gates a message crossed, `would_block` when a blocked link passed all."""

    mode: str
    gates: Tuple[GateOutcome, ...]
    links: Tuple[LinkVerdict, ...]
    blocked_links: Tuple[str, ...]
    would_block: bool
    answer: str = ""

    @property
    def feature(self) -> GateOutcome:
        """The first gate, whether the feature is on, which every evaluation walks."""
        return self.gates[0]

    def gate(self, key: str) -> Optional[GateOutcome]:
        """The gate named `key`, or None when the evaluation stopped before it."""
        return next((gate for gate in self.gates if gate.key == key), None)


@dataclass(frozen=True)
class CustomLink:
    """A link the admin added, matched by its domain or exactly."""

    link: str
    match: str


@dataclass(frozen=True)
class BlockedLinksStats:
    """The link blocker's figures: all time, recent, the top websites and member."""

    total: int
    recent: int
    top_hosts: Tuple[Tuple[str, int], ...]
    top_user: Optional[Tuple[str, int]]
    not_deleted: int


def matches_domain(link: ParsedLink, domain: str) -> bool:
    """The domain itself, a subdomain, or a quick-pick alias host."""
    aliases = {
        "twitter.com": ["x.com"],
        "youtube.com": ["youtu.be"],
        "discord.gg": ["discord.com"],
        "spotify.com": ["open.spotify.com"],
    }
    candidates = [domain] + aliases.get(domain, [])
    return any(
        link.host == candidate or link.host.endswith(f".{candidate}")
        for candidate in candidates
    )


def matches_exact(link: ParsedLink, stored: ParsedLink) -> bool:
    """Same host and path; the stored query params are a subset of the link's."""
    if link.host != stored.host or link.path != stored.path:
        return False
    return all(link.query.get(key) == value for key, value in stored.query.items())


def _custom_entry_rules(config: Mapping[str, Any]) -> List[CustomLink]:
    rules = []
    for entry in list_values(config, constants.BLOCK_LINKS_CUSTOM_LINKS_KEY):
        link_value = entry.get(constants.BLOCK_LINKS_LINK_KEY)
        if not link_value:
            continue
        exact = entry.get(constants.BLOCK_LINKS_MATCH_TYPE_KEY) == "exact"
        rules.append(CustomLink(link=link_value, match="exact" if exact else "domain"))
    return rules


def _first_matching_rule(
    link: ParsedLink, rules: List[CustomLink]
) -> Optional[CustomLink]:
    for rule in rules:
        stored = parse_link(rule.link)
        if rule.match == "exact":
            if matches_exact(link, stored):
                return rule
        elif matches_domain(link, stored.host):
            return rule
    return None


def link_verdicts(links: List[str], config: Mapping[str, Any]) -> List[LinkVerdict]:
    """Per-link decision under this normalized config, with the deciding rule."""
    saved = document_values(config)
    mode = saved.get(constants.BLOCK_LINKS_MODE_KEY) or "block_all"
    rules = _custom_entry_rules(config)
    domains = ensure_list(saved.get(constants.BLOCK_LINKS_ALLOWED_LINKS_KEY))

    verdicts = []
    for raw in links:
        link = parse_link(raw)
        rule = _first_matching_rule(link, rules)

        if mode == "allow_all":
            if rule:
                verdicts.append(
                    LinkVerdict(
                        raw=raw,
                        host=link.host,
                        blocked=True,
                        reason="blocked-by-custom",
                        rule=rule.link,
                        match=rule.match,
                    )
                )
            else:
                verdicts.append(
                    LinkVerdict(
                        raw=raw,
                        host=link.host,
                        blocked=False,
                        reason="allowed-by-default",
                    )
                )
            continue

        domain = next(
            (allowed for allowed in domains if matches_domain(link, allowed)), None
        )
        if domain:
            verdicts.append(
                LinkVerdict(
                    raw=raw,
                    host=link.host,
                    blocked=False,
                    reason="allowed-by-popular",
                    rule=domain,
                    match="domain",
                )
            )
        elif rule:
            verdicts.append(
                LinkVerdict(
                    raw=raw,
                    host=link.host,
                    blocked=False,
                    reason="allowed-by-custom",
                    rule=rule.link,
                    match=rule.match,
                )
            )
        else:
            verdicts.append(
                LinkVerdict(
                    raw=raw,
                    host=link.host,
                    blocked=True,
                    reason="blocked-no-rule",
                )
            )
    return verdicts


def _envelope(
    config: Mapping[str, Any], key: str, style: Optional[str]
) -> Dict[str, Any]:
    current = config.get(key)
    if isinstance(current, dict):
        return {**current, "values": ensure_list(current.get("values"))}

    envelope: Dict[str, Any] = {"values": ensure_list(current)}
    if style:
        envelope["style"] = style
    return envelope


def normalize_block_links_config(cogs: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """A copy of the saved document with its legacy labels and envelopes upgraded."""
    config = dict(cogs or {})

    if not config.get(constants.BLOCK_LINKS_MODE_KEY):
        config[constants.BLOCK_LINKS_MODE_KEY] = "block_all"
        legacy_value = config.get(constants.BLOCK_LINKS_ALLOWED_LINKS_KEY)
        if not isinstance(legacy_value, dict):
            label_domains = {
                "facebook": "facebook.com",
                "instagram": "instagram.com",
                "twitter": "twitter.com",
                "twitch": "twitch.tv",
                "youtube": "youtube.com",
                "discord": "discord.gg",
                "spotify": "spotify.com",
                "tiktok": "tiktok.com",
                "reddit": "reddit.com",
            }
            domains = [
                label_domains[label.lower()]
                for label in ensure_list(legacy_value)
                if isinstance(label, str) and label.lower() in label_domains
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
    config.pop(constants.BLOCK_LINKS_ADD_CUSTOM_KEY, None)
    return config


def evaluate_message(
    subject: MessageSubject, cogs: Optional[Mapping[str, Any]], *, full: bool = False
) -> BlockLinksEvaluation:
    """The message through every gate, stopping at the first failure unless `full`."""
    config = normalize_block_links_config(cogs)
    saved = document_values(config)
    mode = saved.get(constants.BLOCK_LINKS_MODE_KEY) or "block_all"
    answer = saved.get(constants.BLOCK_LINKS_ANSWER_KEY) or ""

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

    exempt_roles = saved[constants.BLOCK_LINKS_ALLOWED_ROLES_KEY]
    matched_roles = tuple(
        role_id for role_id in subject.author_role_ids if role_id in exempt_roles
    )
    role_exempt = check_two_lists_intersection(
        list(subject.author_role_ids), exempt_roles
    )
    gates.append(
        GateOutcome(
            "role",
            not role_exempt,
            "role-exempt" if role_exempt else "role-not-exempt",
            matched_roles,
        )
    )
    if role_exempt and not full:
        return finish()

    channel_exempt = (
        subject.channel_id in saved[constants.BLOCK_LINKS_ALLOWED_CHATS_KEY]
    )
    gates.append(
        GateOutcome(
            "channel",
            not channel_exempt,
            "channel-exempt" if channel_exempt else "channel-not-exempt",
            (subject.channel_id,),
        )
    )
    if channel_exempt and not full:
        return finish()

    message_links = get_message_links(subject.content)
    gates.append(
        GateOutcome(
            "links",
            bool(message_links),
            "has-links" if message_links else "no-links",
            tuple(message_links),
        )
    )
    if not message_links:
        return finish()

    verdicts = link_verdicts(message_links, config)
    blocked = [verdict.raw for verdict in verdicts if verdict.blocked]
    gates.append(
        GateOutcome(
            "rules",
            bool(blocked),
            "some-blocked" if blocked else "all-allowed",
            tuple(blocked),
        )
    )
    return finish()


async def check_sent(sent: MessageSent) -> None:
    """Check a message someone sent in a server."""
    if sent.guild_id is not None:
        await check_message(sent.guild_id, sent.message)


async def check_edited(edited: MessageEdited) -> None:
    """Check a message again once it was edited in a server."""
    if edited.guild_id is not None:
        await check_edited_message(edited.client, edited.payload)


async def check_message(guild_id: str, message: discord.Message) -> None:
    """Delete `message` when it carries a link the guild blocks."""
    cogs = await cache.read_cog_data(guild_id, "block_links")

    evaluation = evaluate_message(MessageSubject.from_message(message), cogs)
    if not evaluation.would_block:
        return

    blocked = [verdict for verdict in evaluation.links if verdict.blocked]
    context = ErrorContext.from_message(
        flow="block_links",
        message=message,
        blocked_hosts=sorted({verdict.host for verdict in blocked})[:3],
        blocked_count=len(blocked),
    )

    answer = fill_placeholders(evaluation.answer, {"user": message.author.mention})

    deleted = False
    try:
        await message.delete()
        deleted = True
        if answer:
            await message.channel.send(
                answer,
                delete_after=5,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    roles=False,
                    users=[message.author],
                    replied_user=False,
                ),
            )
    except Exception as error:
        logger.error(
            f"Failed to block link: {type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise
    finally:
        await to_thread(
            record_blocked_links, guild_id, message, evaluation, deleted=deleted
        )


def record_blocked_links(
    guild_id: str,
    message: discord.Message,
    evaluation: BlockLinksEvaluation,
    *,
    deleted: bool,
) -> None:
    """Record each blocked link, a failed delete included, and never raise."""
    try:
        author = getattr(message, "author", None)
        channel = getattr(message, "channel", None)
        user_id = str(getattr(author, "id", ""))
        blocked = [verdict for verdict in evaluation.links if verdict.blocked]
        recorded = blocked[: constants.BLOCK_LINKS_EVENTS_MAX_PER_MESSAGE]

        for verdict in recorded:
            analytics.record(
                "link.blocked",
                guild_id=guild_id,
                cog="block_links",
                actor=analytics.actor("user", user_id),
                props={
                    "channel_id": str(getattr(channel, "id", "")),
                    "link": verdict.raw,
                    "host": verdict.host,
                    "mode": evaluation.mode,
                    "reason": verdict.reason,
                    "rule": verdict.rule,
                    "match": verdict.match,
                    "deleted": deleted,
                },
            )

        if blocked and not deleted:
            analytics.record(
                "value.blocked_by_permission",
                guild_id=guild_id,
                cog="block_links",
                props={"error_type": "Forbidden"},
            )
    except Exception as error:
        logger.error(
            f"Failed to record blocked links: {type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )


async def check_edited_message(
    bot: discord.Client, payload: discord.RawMessageUpdateEvent
) -> None:
    """Re-run the check when a message is edited after being sent."""
    if not payload.guild_id:
        return

    content = payload.data.get("content")
    if not content or not get_message_links(content):
        return

    channel = bot.get_channel(payload.channel_id)
    if channel is None or isinstance(
        channel,
        (discord.ForumChannel, discord.CategoryChannel, discord.abc.PrivateChannel),
    ):
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except discord.HTTPException:
        return

    if message is None or message.author is None or message.author.bot:
        return

    if getattr(message.author, "roles", None) is None:
        member = await members.find(channel.guild, message.author.id)
        if member is not None:
            message.author = member

    await check_message(str(payload.guild_id), message)


def _mode_label(mode: str, locale: str) -> str:
    for step in registry.get("block_links").steps:
        if not isinstance(step, CardStep):
            continue

        for section in step.sections:
            if section.key != constants.BLOCK_LINKS_MODE_KEY:
                continue
            for option in section.choices():
                if str(option.value) == mode:
                    return option.label.get(locale)
    return mode


def _link_reason_key(verdict: LinkVerdict) -> str:
    if verdict.reason in (
        "allowed-by-custom",
        "blocked-by-custom",
    ):
        return f"{verdict.reason}-{verdict.match or 'domain'}"
    return verdict.reason


def _check_copy() -> Response:
    return i18n.block("block_links.ui.responses.check", Response)


def _line(copy: Response, field: str, reason: str, locale: str, **values: Any) -> str:
    written = copy.fields[field].lines[reason.replace("-", "_")]
    return fill(written.get(locale), **values)


def parse_evaluation_to_fields(
    evaluation: BlockLinksEvaluation, locale: str
) -> Dict[str, str]:
    """The value of the diagnostic's fields the code fills, the exemptions and each
    link's fate; the feature's field is the copy's, chosen by the facts."""
    copy = _check_copy()
    exemptions = []
    role = evaluation.gate("role")
    if role:
        exemptions.append(
            _line(
                copy,
                "exemptions",
                role.reason,
                locale,
                roles=", ".join(f"<@&{value}>" for value in role.detail),
            )
        )
    channel = evaluation.gate("channel")
    if channel:
        exemptions.append(
            _line(
                copy,
                "exemptions",
                channel.reason,
                locale,
                channel=f"<#{channel.detail[0]}>" if channel.detail else "-",
            )
        )

    limit = constants.BLOCK_LINKS_DIAGNOSTIC_MAX_LINKS
    link_lines = [
        _line(
            copy,
            "links",
            _link_reason_key(verdict),
            locale,
            link=verdict.raw[:100],
            rule=verdict.rule or "",
        )
        for verdict in evaluation.links[:limit]
    ]
    if not link_lines:
        link_lines = [_line(copy, "links", "none", locale)]
    if len(evaluation.links) > limit:
        link_lines.append(
            _line(copy, "links", "more", locale, count=len(evaluation.links) - limit)
        )

    return {
        "exemptions": "\n".join(exemptions) or "-",
        "links": "\n".join(link_lines),
    }


def link_check_embed(evaluation: BlockLinksEvaluation, locale: str) -> discord.Embed:
    """The diagnostic of one message: whether it would be blocked, the feature's
    state, the exemptions and each link's fate."""
    return response_embed(
        "block_links.ui.responses.check",
        locale,
        image=True,
        facts=command_facts(
            would_block=evaluation.would_block,
            feature_state=evaluation.feature.reason.replace("-", "_"),
            mode=_mode_label(evaluation.mode, locale),
        ),
        fields=parse_evaluation_to_fields(evaluation, locale),
    )


async def send_link_check_message(
    interaction: discord.Interaction, message: discord.Message
) -> None:
    """Message context menu: why a link of this message was (not) blocked."""
    analytics.record(
        "feature.tested",
        guild_id=interaction.guild_id,
        cog="block_links",
        actor=analytics.actor("user", interaction.user.id),
        props={"source": "context_menu", "surface": "diagnostic"},
    )
    locale = parse_locale(str(interaction.locale))
    cogs = await cache.read_cog_data(str(interaction.guild_id), "block_links", True)
    evaluation = evaluate_message(MessageSubject.from_message(message), cogs, full=True)

    embed = link_check_embed(evaluation, locale)
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def get_blocked_link_records(
    guild_id: str, user_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """The server's latest blocked links, or one member's, newest first."""
    return await data().events_activity.find_by_guild(
        guild_id,
        cog="block_links",
        types=["link.blocked"],
        limit=constants.BLOCK_LINKS_EVENTS_READ_LIMIT,
        actor_id=user_id,
    )


def parse_blocked_link_records(
    records: Sequence[Mapping[str, Any]], locale: str
) -> Dict[str, str]:
    """Records as the {field name: field value} dict PaginationView renders."""
    record_copy = i18n.block("block_links.ui.records.blocked_list", Records).lines
    fields = {}
    for index, record in enumerate(records, start=1):
        props = record.get("props") or {}
        icon = "⚠️" if props.get("deleted") is False else "🚫"
        title = f"{index}. {icon} {props.get('host') or '-'}"

        lines = [f"`{props.get('link') or '-'}`"]
        lines.append(
            fill(
                record_copy["line"].get(locale),
                user=f"<@{(record.get('actor') or {}).get('id')}>",
                channel=f"<#{props.get('channel_id')}>",
                date=format_discord_timestamp(record.get("ts")),
            )
        )
        if props.get("rule"):
            lines.append(fill(record_copy["rule"].get(locale), rule=props["rule"]))
        if props.get("deleted") is False:
            lines.append(record_copy["not_deleted"].get(locale))

        fields[title] = "\n".join(lines)
    return fields


async def get_blocked_links_stats(guild_id: str) -> BlockedLinksStats:
    """The all-time total from the server's lifetime, the rest from the last 90 days."""
    since = datetime.now(timezone.utc) - timedelta(
        seconds=constants.EVENTS_ACTIVITY_TTL_SECONDS
    )
    hosts = await _blocks_by(guild_id, "props.host", since)
    users = await _blocks_by(guild_id, "actor.id", since)
    deleted = dict(await _blocks_by(guild_id, "props.deleted", since))
    recent = sum(deleted.values())
    profile = await data().analytics_lifetime.find_lifetime(guild_id) or {}
    features = profile.get("features") or {}
    blocked = (features.get("block_links") or {}).get("value_count") or 0

    return BlockedLinksStats(
        total=blocked or recent,
        recent=recent,
        top_hosts=tuple((host, count) for host, count in hosts if host)[:3],
        top_user=next(((user, count) for user, count in users if user), None),
        not_deleted=deleted.get(False, 0),
    )


async def _blocks_by(
    guild_id: str, field: str, since: Optional[datetime] = None
) -> List[Tuple[Any, int]]:
    return await data().events_activity.count_by_field(
        guild_id, "block_links", field, types=["link.blocked"], since=since
    )


async def blocked_links_stats_lines(guild_id: str, locale: str) -> List[str]:
    """The link blocker's own figures for its statistics."""
    stats = await get_blocked_links_stats(guild_id)
    if not stats.total:
        return figure_lines("block_links", [], locale)

    top_hosts = (
        ", ".join(f"`{host}` ({count})" for host, count in stats.top_hosts) or "-"
    )
    top_user = (
        f"<@{stats.top_user[0]}> ({stats.top_user[1]})" if stats.top_user else "-"
    )
    figures = [
        Figure("total", "🚫", stats.total),
        Figure("recent", "📅", stats.recent),
        Figure("top_websites", "🌐", top_hosts),
        Figure("top_member", "🙋", top_user),
    ]
    if stats.not_deleted:
        figures.append(Figure("not_deleted", "⚠️", stats.not_deleted))
    return figure_lines("block_links", figures, locale)


async def send_blocked_links_message(
    interaction: discord.Interaction, user_id: Optional[str] = None
) -> None:
    """The server's blocked links, browsable and filtered by member."""
    browser = RecordsBrowser(
        fetch=lambda pressed, member_id: get_blocked_link_records(
            str(pressed.guild_id), user_id=member_id
        ),
        to_fields=lambda records, pressed: parse_blocked_link_records(
            records, parse_locale(str(pressed.locale))
        ),
        copy="block_links.ui.records.blocked_list",
    )
    await browser.send(interaction, user_id=user_id)


async def manager(interaction: discord.Interaction) -> None:
    """The slash command: the setup form, or the manager of what is saved."""
    await open_feature(interaction, "block_links")


class BlockLinksFeature(GenericCogFeature):
    """The link blocker: its document predates the card, so reads normalize."""

    panel_asides = {"blocked_list": "🔎"}

    def __init__(self) -> None:
        super().__init__("block_links")

    async def open(self, context: OpenContext) -> Opened:
        """The document as the card understands it, with the panel's info and its
        records button."""
        opened = await super().open(context)
        if opened.document is None:
            return opened
        return replace(opened, document=self.normalized(opened.document))

    def normalized(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """Legacy labels and missing envelopes, upgraded as the bot reads them."""
        return normalize_block_links_config(document)

    def asides(self) -> Mapping[str, AsideAction]:
        """The records browser, which answers on its own, beside the statistics."""

        async def blocked_list(interaction: Any, _responses: Any) -> None:
            await send_blocked_links_message(interaction)

        return {
            **super().asides(),
            "blocked_list": AsideAction(
                blocked_list,
                own_response=True,
                cooldown=view_constants.ACTION_COOLDOWN_SECONDS,
            ),
        }

    async def stats_lines(self, guild_id: str, locale: str) -> Sequence[str]:
        """The blocks of all time and of the last days, the sites and the member."""
        return await blocked_links_stats_lines(guild_id, locale)

    async def after_disable(
        self, _document: Mapping[str, Any], context: CommitContext
    ) -> None:
        """The blocked links it recorded go with the feature."""
        await data().events_activity.delete_by_guild(context.guild_id, "block_links")


FEATURE = BlockLinksFeature()
