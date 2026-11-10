"""Run a decision's effects against Discord, one message per session.

The executor owns the choreography the review named: a screen of the same
flavour is edited in place, a change of flavour sends the new message before
deleting the old one, a modal is always the first answer to its interaction,
and every final state takes the controls off the message.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

import discord

from app import logger
from app.components.embed import base_embed, response_error_embed, with_footer
from app.constants import DiscordLimits, Style
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants as view_constants
from app.services.utils import parse_command_event_description
from app.settings.discord import views
from app.settings.form.components import Screen
from app.settings.form.copy import error_message, text
from app.settings.form.effects import (
    Ack,
    Commit,
    Confirm,
    Dismiss,
    Effect,
    Finalize,
    Notice,
    OpenChild,
    OpenModal,
    OpenPanel,
    Render,
    ResumeChild,
    ResumeParent,
    ReturnToOpener,
    RunAside,
    ShowError,
)
from app.settings.form.form_state import FormSession

EDITS = ("edit", "edit_item")


@dataclass(frozen=True)
class Hold:
    """The controls a click disabled on its own message while the adapter waits."""

    view: Any = None
    items: tuple[Any, ...] = ()


Answered = tuple[Literal["in_time", "deferred", "failed"], BaseException | None]


class Answering:
    """The one first answer of an interaction, shared by its handler and its clock."""

    def __init__(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.clock: asyncio.Task[Answered] | None = None
        self.held = Hold()
        self._lock = asyncio.Lock()

    async def first(self, answer: Callable[[], Awaitable[Any]]) -> bool:
        """Give `answer` as the first answer unless one was given; True when it was."""
        async with self._lock:
            # discord.py sets is_done() only once the answer's round trip returns.
            if self.interaction.response.is_done():
                return False
            await answer()
            return True

    def start_clock(self) -> None:
        """Defer the interaction when nothing answered it before Discord's deadline."""
        self.clock = asyncio.ensure_future(self._defer_if_late())

    async def hand_over(self) -> None:
        """Stop the clock, for a handler that gives the first answer itself."""
        async with self._lock:
            self.stop()

    def stop(self) -> None:
        """Stop the clock."""
        if self.clock is not None:
            self.clock.cancel()

    def outcome(self) -> Answered:
        """How the interaction was answered: in time, deferred by the clock, failed."""
        clock = self.clock
        if (
            clock is not None
            and clock.done()
            and not clock.cancelled()
            and clock.exception() is None
        ):
            return clock.result()
        return "in_time", None

    async def _defer_if_late(self) -> Answered:
        await asyncio.sleep(view_constants.DEFER_AFTER_SECONDS)
        try:
            deferred = await self.first(
                lambda: self.interaction.response.defer(ephemeral=True)
            )
        except discord.HTTPException as error:
            logger.warn(
                f"Could not answer in time: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            return "failed", error
        return ("deferred", None) if deferred else ("in_time", None)


@dataclass
class Surface:
    """The message a session draws on, and how it was sent."""

    message: Any = None
    message_id: int | None = None
    is_layout: bool = False
    view: Any = None
    embed: discord.Embed | None = None
    confirmation_id: int | None = None
    replace_next: bool = False
    webhook: discord.Webhook | None = None
    touched_at: float | None = None


@dataclass
class Outcome:
    """What running one effect reported."""

    effect: str
    succeeded: bool
    duration_ms: int
    error: str | None = None
    reported: bool = False


class EffectFailed(Exception):
    """An effect failure already logged by the run it happened in."""

    def __init__(self, error: Exception) -> None:
        super().__init__(repr(error))
        self.error = error


AsideRunner = Callable[[discord.Interaction, str, FormSession, str], Awaitable[None]]
CommitRunner = Callable[
    [discord.Interaction, str, Mapping[str, Any], FormSession], Awaitable[None]
]
Continue = Callable[[discord.Interaction, FormSession, Any], Awaitable[None]]
Ending = Callable[[], Awaitable[discord.ui.LayoutView]]


@dataclass
class Executor:
    """Runs effects for one interaction, reporting each outcome."""

    interaction: discord.Interaction
    session: FormSession
    surface: Surface
    dispatcher: views.Dispatcher
    locale: str
    key: str
    commit: CommitRunner
    aside: AsideRunner
    continue_with: Continue
    answering: Answering
    ending: Ending | None = None
    back: Ending | None = None
    outcomes: list[Outcome] = field(default_factory=list)

    async def run(self, effects: tuple[Effect, ...]) -> None:
        """Execute every effect, a modal first: it must be the interaction's answer."""
        self.surface.webhook = self.interaction.followup
        self.surface.touched_at = time.monotonic()
        ordered = sorted(effects, key=lambda effect: not isinstance(effect, OpenModal))
        if self.ending is not None and any(
            isinstance(effect, OpenPanel) for effect in ordered
        ):
            ordered = [effect for effect in ordered if not isinstance(effect, Finalize)]

        for effect in ordered:
            name = type(effect).__name__
            started = time.monotonic()

            try:
                await self._execute(effect)
                self.outcomes.append(Outcome(name, True, _ms(started)))
            except EffectFailed as failed:
                self.outcomes.append(
                    Outcome(name, False, _ms(started), str(failed), reported=True)
                )
                raise
            except Exception as error:
                self.outcomes.append(Outcome(name, False, _ms(started), repr(error)))
                raise

    async def _execute(self, effect: Effect) -> None:
        if isinstance(effect, OpenPanel) and self.ending is not None:
            await self.hand_back(await self.ending())
        elif isinstance(effect, ReturnToOpener):
            await self.return_to_opener()
        elif isinstance(effect, (OpenChild, OpenPanel, ResumeParent, ResumeChild)):
            await self.continue_with(self.interaction, self.session, effect)
        elif isinstance(effect, Commit):
            if effect.kind in EDITS and not effect.quiet:
                await self.answering.first(
                    lambda: self.response.defer(thinking=True, ephemeral=True)
                )
            await self.commit(
                self.interaction, effect.kind, effect.payload, self.session
            )
        elif isinstance(effect, RunAside):
            await self.aside(self.interaction, effect.name, self.session, effect.target)
        else:
            await self._draw(effect)

    async def _draw(self, effect: Effect) -> None:
        if isinstance(effect, Render):
            await self.render(effect.screen)
        elif isinstance(effect, OpenModal):
            await self.open_modal(effect)
        elif isinstance(effect, ShowError):
            await self.show_error(effect)
        elif isinstance(effect, Notice):
            await self.notice(effect.key)
        elif isinstance(effect, Confirm):
            await self.confirm(effect.screen)
        elif isinstance(effect, Dismiss):
            await self.dismiss()
        elif isinstance(effect, Finalize):
            await self.finalize(effect.kind)
        elif isinstance(effect, Ack):
            await self.ack()

    @property
    def response(self) -> Any:
        """The interaction's first answer."""
        return self.interaction.response

    @property
    def followup(self) -> Any:
        """The interaction's webhook, for everything after the first answer."""
        return self.interaction.followup

    def _ids(self) -> views.Ids:
        return views.Ids(self.session.id, self.session.revision, self.session.cursor)

    async def ack(self) -> None:
        """Acknowledge without changing the screen."""
        await self.answering.first(self.response.defer)

    def _on_own_message(self) -> bool:
        message = self.interaction.message
        return message is not None and message.id == self.surface.message_id

    async def _send(self, **payload: Any) -> Any:
        if await self.answering.first(
            lambda: self.response.send_message(ephemeral=True, **payload)
        ):
            return await self.interaction.original_response()

        delete_after = payload.pop("delete_after", None)
        message = await self.followup.send(ephemeral=True, **payload)
        if delete_after is not None:
            await message.delete(delay=delete_after)
        return message

    async def _edit(self, **payload: Any) -> None:
        if self._on_own_message() and await self.answering.first(
            lambda: self.response.edit_message(**payload)
        ):
            return
        await self.ack()
        await self.followup.edit_message(self.surface.message_id, **payload)

    async def _delete(self, message_id: int | None) -> None:
        if message_id is None:
            return
        try:
            await self.followup.delete_message(message_id)
        except discord.HTTPException as error:
            logger.warn(
                f"Could not remove the previous message: "
                f"{type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

    async def _replace(self, **payload: Any) -> None:
        """Send the new screen, then remove the one it replaces."""
        old = self.surface.message_id
        await self.ack()
        message = await self.followup.send(ephemeral=True, **payload)
        self._own(message)
        await self._delete(old)

    def _own(self, message: Any) -> None:
        self.surface.message = message
        self.surface.message_id = message.id

    def _payload(self, screen: Screen) -> dict[str, Any]:
        if screen.is_layout:
            return {"view": views.layout_of(screen, self._ids(), self.dispatcher)}
        return {
            "embed": views.embed_of(screen),
            "view": views.view_of(screen, self._ids(), self.dispatcher),
        }

    async def render(self, screen: Screen) -> None:
        """First send, then edit in place, and replace when the flavour changes."""
        payload = self._payload(screen)
        if self.surface.message_id is None:
            self._own(await self._send(**payload))
        elif screen.is_layout != self.surface.is_layout or self.surface.replace_next:
            await self._replace(**payload)
        else:
            await self._edit(**payload)

        self.surface.view, self.surface.embed = payload["view"], payload.get("embed")
        self.surface.is_layout = screen.is_layout
        self.surface.replace_next = False

    async def hand_back(self, view: discord.ui.LayoutView) -> None:
        """Give the message back to whoever opened the session, drawn with `view`."""
        if self.surface.is_layout and self.surface.message_id is not None:
            await self._edit(view=view)
        else:
            await self._replace(view=view)
        self.surface.view, self.surface.embed = None, None
        self.surface.is_layout = True

    async def return_to_opener(self) -> None:
        """Draw what the opener's `back` returns on the session's message."""
        assert self.back is not None
        await self.hand_back(await self.back())

    async def open_modal(self, effect: OpenModal) -> None:
        """Open the modal as the interaction's first answer."""
        modal = views.modal_of(
            effect.screen, self._ids(), self.dispatcher, effect.action, effect.arg
        )
        if not await self.answering.first(lambda: self.response.send_modal(modal)):
            raise EffectFailed(
                RuntimeError("a modal cannot follow an answered interaction")
            )

    async def show_error(self, effect: ShowError) -> None:
        """An ephemeral error, as an embed or a plain sentence."""
        payload: dict[str, Any]
        if effect.plain:
            payload = {"content": error_message(effect.key, effect.args, self.locale)}
        else:
            payload = {"embed": response_error_embed(effect.key, self.locale)}
        if effect.delete_after is not None:
            payload["delete_after"] = effect.delete_after
        await self._send(**payload)

    async def notice(self, key: str) -> None:
        """A short self-deleting note, for a click that changed nothing."""
        await self._send(
            content=text(f"commands.form-notices.{key}", self.locale),
            delete_after=view_constants.ACTION_NOTICE_SECONDS,
        )

    async def confirm(self, screen: Screen) -> None:
        """The keep-or-discard question, on a message of its own."""
        await self.ack()
        view = views.view_of(screen, self._ids(), self.dispatcher)
        message = await self.followup.send(
            embed=views.embed_of(screen), view=view, ephemeral=True
        )
        self.surface.confirmation_id = message.id

    async def dismiss(self) -> None:
        """Remove the confirmation the interaction came from."""
        await self.ack()
        await self.interaction.delete_original_response()
        self.surface.confirmation_id = None

    async def _strip_controls(self) -> None:
        if self.surface.is_layout and self.surface.view is not None:
            await self._edit(view=views.finalized_layout(self.surface.view))
        else:
            await self._edit(view=None)
        self.surface.view = None

    def _event_copy(self, kind: str) -> tuple[str, str]:
        title = text(f"commands.command-events.{kind}.title", self.locale)
        description = text(f"commands.command-events.{kind}.description", self.locale)
        message = self.interaction.message
        when = (
            (message.edited_at or message.created_at)
            if message is not None
            else discord.utils.utcnow()
        )

        return (
            parse_command_event_description(title, when, self.interaction, self.key),
            parse_command_event_description(
                description, when, self.interaction, self.key
            ),
        )

    def _fresh_embed(self) -> discord.Embed:
        return discord.Embed(color=int(Style.BACKGROUND_COLOR, base=16))

    def _closed(self, embed: discord.Embed) -> discord.Embed:
        """The same embed with the report line as its last subtext line."""
        return with_footer(
            embed, text("commands.commands.commons.embed.footer", self.locale)
        )

    async def finalize(self, kind: str) -> None:
        """The final state of the session, controls gone."""
        if kind == "discarded":
            await self._strip_controls()
            if self.surface.confirmation_id is not None:
                await self.interaction.delete_original_response()
            return
        if kind in ("expired", "closed"):
            await self._close_with(kind)
            return
        if kind == "preview":
            await self._strip_controls()
            return
        if kind in ("error", "duplicate"):
            error_key = (
                "item-already-registered"
                if kind == "duplicate"
                else "command-generic-error"
            )
            await self._final(
                response_error_embed(error_key, self.locale),
                replace=self.surface.is_layout,
            )
            return
        await self._final(*self._final_embed(kind))

    def _final_embed(self, kind: str) -> tuple[discord.Embed, bool]:
        title, description = self._event_copy(kind)
        if kind == "enabled":
            embed = self.surface.embed or self._fresh_embed()
            embed.clear_fields()
            embed.title, embed.description = title, description

            return self._closed(embed), self.surface.is_layout
        embed = self._fresh_embed()
        embed.title, embed.description = title, description
        return self._closed(embed), True

    async def _final(self, embed: discord.Embed, replace: bool) -> None:
        if replace or self.surface.message_id is None:
            await self._replace(embed=embed)
        else:
            await self._edit(embed=embed, view=None)
        self.surface.is_layout = False
        self.surface.view, self.surface.embed = None, embed

    async def _close_with(self, key: str) -> None:
        await self._edit(**closed_payload(self.surface, key, self.locale))
        self.surface.view = None


async def hold(surface: Surface, answering: Answering) -> None:
    """Answer first by disabling the controls of the message clicked, else defer."""
    interaction = answering.interaction
    message = interaction.message
    if message is None or message.id != surface.message_id or surface.view is None:
        await answering.first(interaction.response.defer)
        return
    view = surface.view
    items = views.disable(view)

    if await answering.first(lambda: interaction.response.edit_message(view=view)):
        answering.held = Hold(view, items)
        return
    views.enable(items)


async def release(surface: Surface, answering: Answering) -> None:
    """Give the controls back when nothing was drawn over the message they disabled."""
    held = answering.held
    answering.held = Hold()
    if not held.items or surface.view is not held.view or surface.message_id is None:
        return
    views.enable(held.items)
    try:
        await answering.interaction.followup.edit_message(
            surface.message_id, view=held.view
        )
    except discord.HTTPException as error:
        logger.warn(
            f"Could not enable a form message again: {type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )


def closed_payload(surface: Surface, key: str, locale: str) -> dict[str, Any]:
    """The message as it looks once its session closed: no controls, its notice."""
    notice = text(f"commands.form-notices.{key}", locale)
    if surface.is_layout and surface.view is not None:
        view = views.finalized_layout(surface.view)
        container = cast(Any, view.children[0]) if view.children else None

        if container is not None:
            container.add_item(discord.ui.TextDisplay(notice))
        return {"view": view}
    embed = surface.embed or base_embed("", "")
    embed.description = f"{embed.description or ''}\n\n{notice}".strip()
    return {"embed": embed, "view": None}


async def expire_surface(surface: Surface, locale: str) -> None:
    """Close the message of a session that expired, while Discord still allows it."""
    if surface.message_id is None or surface.view is None:
        return
    age = time.monotonic() - (surface.touched_at or 0.0)
    if surface.webhook is None or age >= DiscordLimits.INTERACTION_TOKEN_SECONDS:
        surface.view = None
        return
    try:
        payload = closed_payload(surface, "expired", locale)
        await surface.webhook.edit_message(surface.message_id, **payload)
    except discord.HTTPException as error:
        logger.warn(
            f"Could not expire a form message: {type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
    surface.view = None


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
