"""Where cogs and buttons enter the platform, and where every click comes back.

`open_feature` replaces the seven `service.manager` functions: it asks the
feature what is saved, opens a setup or a manage session, and draws the first
screen. `handle` is the single callback of every component the renderer
built: decode, lock, prefetch, decide, run.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

import discord

from app import logger
from app.components.embed import response_error_embed
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants as view_constants
from app.services import analytics
from app.services.trace import trace_scope
from app.services.utils import is_guild_admin
from app.settings.discord import observability
from app.settings.discord.interactions import ComponentId, decode, to_event
from app.settings.discord.transitions import (
    EffectFailed,
    Executor,
    Surface,
    expire_surface,
)
from app.settings.discord.views import Dispatcher
from app.settings.features import feature_for
from app.settings.features.feature import (
    AsideAction,
    CommitContext,
    FeatureModule,
    OpenContext,
    Opened,
)
from app.settings.form import events as ev
from app.settings.form.components import Gallery
from app.settings.form.copy import normalize_locale, text
from app.settings.form.effects import (
    Finalize,
    OpenChild,
    Render,
    ResumeChild,
    ResumeParent,
)
from app.settings.form.form import Context, Decision, decide
from app.settings.form.form_state import (
    FormSession,
    InMemorySessionStore,
    Manage,
    Origin,
    Setup,
    Status,
    new_session,
)
from app.settings.form.form_yaml import registry
from app.settings.form.lookups import Lookup, lookup_for


@dataclass
class Session:
    """Everything the adapter keeps beside a session: feature, document, message."""

    feature: FeatureModule
    opened: Opened
    surface: Surface
    source: str
    command_name: str
    guild: Any
    member: Any
    is_admin: bool = False
    friction: observability.Friction = field(default_factory=observability.Friction)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cooldowns: dict[str, float] = field(default_factory=dict)
    previews: asyncio.Task[Mapping[str, str]] | None = None

    async def ready_previews(self, wait: bool) -> Mapping[str, str]:
        """The previews drawn in the background, waited for up to their own limit."""
        if self.previews is None:
            return self.opened.previews
        if not self.previews.done() and not wait:
            return {}

        try:
            return dict(
                await asyncio.wait_for(
                    asyncio.shield(self.previews),
                    view_constants.PREVIEW_WAIT_SECONDS,
                )
            )
        except (Exception, asyncio.TimeoutError):
            return {}

    def previews_pending(self) -> bool:
        """Whether the previews are still being drawn in the background."""
        return self.previews is not None and not self.previews.done()

    def forget(self) -> None:
        """Stop what still runs for this session."""
        if self.previews is not None and not self.previews.done():
            self.previews.cancel()


def _draws_a_gallery(decision: Decision) -> bool:
    return any(
        isinstance(effect, Render)
        and any(isinstance(item, Gallery) for item in effect.screen.components)
        for effect in decision.effects
    )


class Runtime:
    """The sessions of this process and what the adapter knows about each."""

    def __init__(self) -> None:
        self.store = InMemorySessionStore()
        self.sessions: dict[str, Session] = {}
        self.dispatcher = Dispatcher(self.handle)

    def reset(self) -> None:
        """Forget every session (tests)."""
        for state in self.sessions.values():
            state.forget()
        self.store = InMemorySessionStore()
        self.sessions.clear()

    async def open_feature(
        self, interaction: discord.Interaction, key: str, source: str | None = None
    ) -> None:
        """Open the feature `key`: the setup form, or the manager of what is saved."""
        locale = normalize_locale(interaction.locale)
        guild = interaction.guild
        member = interaction.user
        guild_id = str(getattr(guild, "id", interaction.guild_id))
        feature = feature_for(key)
        opened = await feature.open(
            OpenContext(guild_id, str(interaction.user.id), locale, guild, member)
        )

        if opened.refusal:
            embed = response_error_embed(opened.refusal, locale)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        definition = registry.get(key)
        mode = Manage() if opened.document is not None else Setup()
        origin = Origin(guild_id, str(interaction.user.id), locale)
        session = new_session(
            (definition.key, definition.version),
            mode,
            origin,
            ttl_seconds=view_constants.LONG_TIMEOUT_SECONDS,
        )
        self.store.create(session)
        self.sessions[session.id] = Session(
            feature=feature,
            opened=opened,
            surface=Surface(),
            source=source or analytics.resolve_source(interaction),
            command_name=_command_label(interaction, key),
            guild=guild,
            member=member,
            is_admin=is_guild_admin(member),
        )
        state = self.sessions[session.id]

        if opened.pending_previews is not None:
            state.previews = asyncio.ensure_future(opened.pending_previews)
        observability.open_journey(
            session, state.command_name, state.source, state.is_admin
        )
        try:
            await self._apply(interaction, session, ev.Started(str(interaction.id)))
        except EffectFailed as failed:
            raise failed.error from None

    async def handle(
        self, interaction: discord.Interaction, custom_id: str, payload: Any
    ) -> None:
        """The callback of every component: decode, lock, decide, run."""
        component = decode(custom_id)
        if component is None:
            return
        session = self.store.get(component.session_id)
        if session is None:
            await self._unknown(interaction)
            return
        state = self.sessions[session.id]
        try:
            await self._handle_locked(interaction, component, session, payload, state)
        except EffectFailed:
            return

    async def _handle_locked(
        self,
        interaction: discord.Interaction,
        component: ComponentId,
        session: FormSession,
        payload: Any,
        state: Session,
    ) -> None:
        async with state.lock:
            current = self.store.get(session.id) or session
            if not current.is_closed and self._due(current, _now()):
                await self._apply(
                    interaction, current, ev.Expired(f"expire:{interaction.id}")
                )
                return
            awaiting = current.awaiting or ""
            section = awaiting if awaiting.startswith("section:") else None
            event = to_event(interaction, component, payload, current.cursor, section)
            await self._apply(interaction, current, event)

    async def _unknown(self, interaction: discord.Interaction) -> None:
        """A click on a message whose session this process no longer has."""
        notice = text(
            "commands.form-notices.expired", normalize_locale(interaction.locale)
        )
        message = interaction.message

        try:
            if message is not None and message.flags.components_v2:
                await interaction.response.defer()
                await interaction.followup.send(content=notice, ephemeral=True)
            else:
                embed = message.embeds[0] if message and message.embeds else None
                if embed is not None:
                    embed.description = f"{embed.description or ''}\n\n{notice}".strip()
                    await interaction.response.edit_message(embed=embed, view=None)
                else:
                    await interaction.response.send_message(
                        content=notice, ephemeral=True
                    )
        except discord.HTTPException as error:
            logger.warn(
                f"Could not finalize an unknown session: "
                f"{type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

    async def _context(
        self, session: FormSession, lookup: Lookup | None = None
    ) -> Context:
        state = self.sessions[session.id]
        opened = state.opened
        document = opened.document or {}
        external: Mapping[str, Mapping[str, Any]] = {}

        if lookup is not None:
            external = await state.feature.prefetch(
                lookup,
                OpenContext(
                    session.origin.guild_id,
                    session.origin.user_id,
                    session.origin.locale,
                    state.guild,
                    state.member,
                ),
            )
        parent_values: Mapping[str, Any] = {}
        parent = self.store.get(session.parent_id) if session.parent_id else None

        if parent is not None:
            from app.settings.form.responses.responses import document_values

            parent_values = {**document_values(document), **parent.values()}
        items = _items(session, parent, document)
        return Context(
            now=_now(),
            ttl_seconds=view_constants.LONG_TIMEOUT_SECONDS,
            document=document,
            parent_values=parent_values,
            items=items,
            external=external,
            server_name=str(getattr(state.guild, "name", "")),
            previews=await state.ready_previews(False),
            panel_rows=opened.rows,
            panel_info=opened.info,
            panel_info_title=opened.info_title,
            extra_buttons=opened.extra_buttons,
            enabled=opened.enabled,
        )

    async def _apply(
        self, interaction: discord.Interaction, session: FormSession, event: ev.Event
    ) -> None:
        state = self.sessions[session.id]
        definition = registry.get(*session.definition)

        async with trace_scope(
            f"{state.command_name}:{type(event).__name__}",
            guild_id=session.origin.guild_id,
            user_id=session.origin.user_id,
            feature=session.key,
            source=state.source,
            session_id=session.id if session.parent_id is None else session.parent_id,
            quiet=True,
            is_admin=state.is_admin,
        ):
            lookup = lookup_for(definition, session, event)
            if lookup is not None and not interaction.response.is_done():
                await interaction.response.defer()
            context = await self._context(session, lookup)
            decision = decide(definition, session, event, context)
            if state.previews_pending() and _draws_a_gallery(decision):
                if not interaction.response.is_done():
                    await interaction.response.defer()
                previews = await state.ready_previews(True)
                context = replace(context, previews=previews, now=_now())
                decision = decide(definition, session, event, context)

            observability.log_decision(decision, event, session)

            if decision.session.revision > session.revision:
                self.store.put(decision.session)
            elif decision.session is not session:
                self.store.remember(decision.session)
            observability.emit(
                decision, decision.session, state.source, state.friction, state.is_admin
            )
            await self._run(interaction, decision)

    async def _run(self, interaction: discord.Interaction, decision: Decision) -> None:
        session = decision.session
        state = self.sessions[session.id]
        executor = Executor(
            interaction=interaction,
            session=session,
            surface=state.surface,
            dispatcher=self.dispatcher,
            locale=session.origin.locale,
            key=session.key,
            commit=self._commit,
            aside=self._aside,
            continue_with=self._continue,
        )

        try:
            await executor.run(decision.effects)
        except EffectFailed:
            raise
        except Exception as error:
            raise EffectFailed(error) from error
        finally:
            observability.log_effects(session, executor.outcomes)
        if session.is_closed and session.parent_id is None:
            self._close(session, decision)

    def _close(self, session: FormSession, decision: Decision) -> None:
        self.sessions[session.id].forget()
        outcomes = {
            Status.CANCELLED: "discarded",
            Status.EXPIRED: "abandoned",
            Status.FAILED: "failure",
        }

        if session.status in outcomes:
            observability.close_journey(session, outcomes[session.status])
        finalize = next(
            (effect for effect in decision.effects if isinstance(effect, Finalize)),
            None,
        )
        if (
            session.status is Status.COMPLETED
            and finalize
            and finalize.kind == "enabled"
        ):
            observability.close_journey(session, "saved")

    async def _continue(
        self, interaction: discord.Interaction, session: FormSession, effect: Any
    ) -> None:
        """Open a child on the same message, or hand a child's result to its parent."""
        state = self.sessions[session.id]
        if isinstance(effect, OpenChild):
            child = effect.session
            self.store.create(child)
            self.sessions[child.id] = Session(
                feature=state.feature,
                opened=state.opened,
                surface=state.surface,
                source=state.source,
                command_name=state.command_name,
                guild=state.guild,
                member=state.member,
                friction=state.friction,
                lock=state.lock,
                previews=state.previews,
            )
            state.surface.replace_next = (
                isinstance(session.mode, Manage) and state.surface.is_layout
            )
            await self._apply(interaction, child, ev.Started(f"child:{child.id}"))

            return
        if isinstance(effect, ResumeChild):
            open_children = [
                child
                for child in self.store.children(session.id)
                if not child.is_closed
            ]
            if open_children:
                child = open_children[-1]
                await self._apply(
                    interaction, child, ev.ScreenRequested(f"again:{interaction.id}")
                )
            return
        assert isinstance(effect, ResumeParent)
        parent = self.store.get(effect.parent_id)
        if parent is None:
            return
        self.sessions[effect.parent_id].surface = state.surface
        event = ev.ChildFinished(
            f"resume:{session.id}",
            None,
            effect.child_mode,
            effect.answers,
            effect.index,
            effect.cancelled,
        )
        await self._apply(interaction, parent, event)

    async def _commit(
        self,
        interaction: discord.Interaction,
        kind: str,
        payload: Mapping[str, Any],
        session: FormSession,
    ) -> None:
        """Run the feature's commit and feed its outcome back to the engine."""
        state = self.sessions[session.id]
        message = interaction.message
        when = (
            (message.edited_at or message.created_at) if message is not None else _now()
        )
        context = CommitContext(
            guild_id=session.origin.guild_id,
            user_id=session.origin.user_id,
            locale=session.origin.locale,
            document=state.opened.document or {},
            when=when,
            source=state.source,
            session_id=session.id if session.parent_id is None else session.parent_id,
        )

        try:
            result = await state.feature.commit(kind, payload, context)
        except Exception as error:
            reason = type(error).__name__
            if reason == "DuplicateItem":
                reason = "duplicate"
            else:
                logger.error(
                    f"form {session.key} commit {kind} failed: {error!r}",
                    log_type=logconstants.COMMAND_ERROR_TYPE,
                    context=observability.error_context(session, commit=kind),
                    exc_info=True,
                )
            await self._apply(
                interaction,
                session,
                ev.CommitFailed(f"commit:{session.id}", None, kind, reason),
            )
            return
        if result.document is not None:
            state.opened = Opened(
                document=result.document,
                rows=state.opened.rows,
                info=state.opened.info,
                info_title=state.opened.info_title,
                extra_buttons=state.opened.extra_buttons,
                enabled=state.opened.enabled,
            )
        await self._apply(
            interaction,
            session,
            ev.CommitSucceeded(f"commit:{session.id}", None, kind, result.written),
        )

    async def _aside(
        self, interaction: discord.Interaction, name: str, session: FormSession
    ) -> None:
        """Help, history, or a feature's own side action, with its cooldown."""
        state = self.sessions[session.id]
        locale = session.origin.locale

        if name == "help":
            await self._help(interaction, session)
            return
        if name == "history":
            await self._history(interaction, session)
            return
        action = state.feature.asides().get(name)
        if action is None:
            await interaction.response.defer()
            return
        if action.cooldown and _cooling(state, name, action.cooldown):
            await interaction.response.send_message(
                embed=_cooldown_embed(locale),
                ephemeral=True,
                delete_after=view_constants.ACTION_NOTICE_SECONDS,
            )
            return
        if action.confirm:
            await self._ask_before(interaction, session, name, action, action.confirm)
            return
        if action.cooldown:
            _cool(state, name)
        if action.defer and not action.own_response:
            await interaction.response.defer()
        responses = state.feature.responses_for_aside(session.answers, locale)
        await action.handler(interaction, responses)

    async def _ask_before(
        self,
        interaction: discord.Interaction,
        session: FormSession,
        name: str,
        action: AsideAction,
        question: str,
    ) -> None:
        """Run a side action only once the admin confirms what it will do."""
        from app.components.embed import response_embed
        from app.views.confirm_action import ConfirmActionView

        state = self.sessions[session.id]
        locale = session.origin.locale

        async def confirmed(answer: discord.Interaction) -> None:
            if action.cooldown and _cooling(state, name, action.cooldown):
                await answer.response.edit_message(
                    embed=_cooldown_embed(locale), view=None
                )
                return
            if action.cooldown:
                _cool(state, name)
            await answer.response.edit_message(view=None)
            responses = state.feature.responses_for_aside(session.answers, locale)
            await action.handler(answer, responses)

        embed = response_embed(question, locale, footer=True, image=True)
        if action.confirm_values and embed.description:
            values = action.confirm_values(state.opened.document or {}, locale)
            for token, value in values.items():
                embed.description = embed.description.replace(f"${token}", value)
        await interaction.response.send_message(
            embed=embed, view=ConfirmActionView(confirmed, locale), ephemeral=True
        )

    async def _help(
        self, interaction: discord.Interaction, session: FormSession
    ) -> None:
        from app.constants import KeikoIcons, Style

        locale = session.origin.locale
        view = self.sessions[session.id].surface.view
        embed = discord.Embed(
            title=f"🙋 {text('buttons.help.label', locale)}",
            description=text("buttons.captions.desc", locale),
            color=int(Style.BACKGROUND_COLOR, base=16),
        )
        embed.set_thumbnail(url=KeikoIcons.IMAGE_02)

        for item in _walk(view):
            if not isinstance(item, discord.ui.Button) or not item.label:
                continue
            desc = getattr(item, "desc", None)
            component = decode(item.custom_id or "")

            if not desc or (component and component.target == "aside:help"):
                continue
            embed.add_field(
                name=f"{item.emoji} {item.label}" if item.emoji else item.label,
                value=desc,
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _history(
        self, interaction: discord.Interaction, session: FormSession
    ) -> None:
        from app.services.cogs import find_cog_events_by_guild
        from app.services.manager import parse_history_data, parse_history_desc
        from app.views.records import RecordsBrowser

        locale = session.origin.locale
        browser = RecordsBrowser(
            fetch=lambda _interaction, _user: find_cog_events_by_guild(
                session.origin.guild_id, session.key
            ),
            to_fields=parse_history_data,
            title=text("buttons.changes-history.label", locale),
            description=parse_history_desc(interaction, session.key),
        )
        await browser.send(interaction)

    async def expire_stale(
        self, now: datetime | None = None
    ) -> tuple[FormSession, ...]:
        """Close every session past its deadline, on screen and in the store."""
        expired: list[FormSession] = []
        moment = now or _now()

        for session in self.store.due(moment):
            if not self._due(session, moment):
                continue
            state = self.sessions[session.id]
            root = session.id if session.parent_id is None else session.parent_id

            async with (
                state.lock,
                trace_scope(
                    f"{state.command_name}:Expired",
                    guild_id=session.origin.guild_id,
                    user_id=session.origin.user_id,
                    feature=session.key,
                    source=state.source,
                    session_id=root,
                    quiet=True,
                ),
            ):
                event = ev.Expired(f"expire:{session.id}")
                definition = registry.get(*session.definition)
                context = await self._context(session)
                decision = decide(definition, session, event, context)
                observability.log_decision(decision, event, session)
                self.store.put(decision.session)
                observability.emit(
                    decision,
                    decision.session,
                    state.source,
                    state.friction,
                    state.is_admin,
                )
                await expire_surface(state.surface, session.origin.locale)
            if session.parent_id is None:
                observability.close_journey(session, "abandoned")
            expired.append(decision.session)
        return tuple(expired)

    def _due(self, session: FormSession, now: datetime) -> bool:
        """Past its deadline, with no open child still inside its own."""
        if session.expires_at > now:
            return False
        return not any(
            not child.is_closed and child.expires_at > now
            for child in self.store.children(session.id)
        )

    async def sweep(self, now: datetime | None = None) -> None:
        """Expire what nobody finished and forget what already ended."""
        moment = now or _now()
        await self.expire_stale(moment)
        for session_id in self.store.forget_closed(moment):
            state = self.sessions.pop(session_id, None)
            if state is not None:
                state.forget()


def _cooling(state: Session, name: str, seconds: int) -> bool:
    last = state.cooldowns.get(name)
    return last is not None and time.monotonic() - last < seconds


def _cool(state: Session, name: str) -> None:
    state.cooldowns[name] = time.monotonic()


def _cooldown_embed(locale: str) -> discord.Embed:
    from app.components.embed import response_embed

    return response_embed("buttons.cooldown", locale)


def _walk(view: Any) -> list[Any]:
    found: list[Any] = []

    def walk(node: Any) -> None:
        for item in getattr(node, "children", None) or []:
            found.append(item)
            walk(item)
            accessory = getattr(item, "accessory", None)

            if accessory is not None:
                found.append(accessory)

    if view is not None:
        walk(view)
    return found


def _items(
    session: FormSession, parent: FormSession | None, document: Mapping[str, Any]
) -> tuple[Mapping[str, Any], ...]:
    """The composition's items: saved ones on the manager, answered ones in a setup."""
    from app.settings.form.responses.responses import items_of

    definition = registry.get(*session.definition)
    composition = definition.composition

    if composition is None:
        return ()
    root = parent or session
    if isinstance(root.mode, Manage):
        return tuple(items_of(document, composition.key))
    answered = root.answers.get(composition.key)
    if answered is None or not answered.raw:
        return ()
    return tuple(
        {key: value.raw for key, value in item.items()} for item in answered.raw
    )


def _command_label(interaction: Any, key: str) -> str:
    command = getattr(interaction, "command", None)
    return str(getattr(command, "qualified_name", None) or key)


def _now() -> datetime:
    return datetime.now(timezone.utc)


RUNTIME = Runtime()


async def open_feature(
    interaction: discord.Interaction, key: str, source: str | None = None
) -> None:
    """Open the feature `key` for the interaction, on the process runtime."""
    await RUNTIME.open_feature(interaction, key, source)
