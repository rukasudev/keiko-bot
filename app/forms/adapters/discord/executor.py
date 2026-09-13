"""Run a decision's effects against Discord, one message per session.

The executor owns the choreography the review named: a screen of the same
flavour is edited in place, a change of flavour sends the new message before
deleting the old one, a modal is always the first answer to its interaction,
and every final state takes the controls off the message.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

import discord

from app import logger
from app.components.embed import base_embed, response_error_embed
from app.constants import KeikoIcons, Style
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants as view_constants
from app.forms.adapters.discord import renderer
from app.forms.engine.effects import (
    Ack,
    Commit,
    Confirm,
    Dismiss,
    Effect,
    Finalize,
    Notice,
    OpenChild,
    OpenModal,
    Render,
    ResumeChild,
    ResumeParent,
    RunAside,
    ShowError,
)
from app.forms.engine.screen import Screen
from app.forms.engine.session import FormSession
from app.forms.extensions.copy import text
from app.forms.kinds import manage
from app.services.utils import parse_command_event_description

EDITS = ("edit", "edit_item")


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


@dataclass
class Outcome:
    """What running one effect reported."""

    effect: str
    ok: bool
    duration_ms: int
    error: str | None = None


AsideRunner = Callable[[discord.Interaction, str, FormSession], Awaitable[None]]
CommitRunner = Callable[
    [discord.Interaction, str, Mapping[str, Any], FormSession], Awaitable[None]
]
Continue = Callable[[discord.Interaction, FormSession, Any], Awaitable[None]]


@dataclass
class Executor:
    """Runs effects for one interaction, reporting each outcome."""

    interaction: discord.Interaction
    session: FormSession
    surface: Surface
    dispatcher: renderer.Dispatcher
    locale: str
    key: str
    commit: CommitRunner
    aside: AsideRunner
    continue_with: Continue
    outcomes: list[Outcome] = field(default_factory=list)

    async def run(self, effects: tuple[Effect, ...]) -> None:
        """Execute every effect, a modal first: it must be the interaction's answer."""
        ordered = sorted(effects, key=lambda effect: not isinstance(effect, OpenModal))
        for effect in ordered:
            name = type(effect).__name__
            started = time.monotonic()
            try:
                await self._execute(effect)
                self.outcomes.append(Outcome(name, True, _ms(started)))
            except Exception as error:
                self.outcomes.append(Outcome(name, False, _ms(started), repr(error)))
                raise

    async def _execute(self, effect: Effect) -> None:
        if isinstance(effect, (OpenChild, ResumeParent, ResumeChild)):
            await self.continue_with(self.interaction, self.session, effect)
        elif isinstance(effect, Commit):
            if effect.kind in EDITS and not self.response.is_done():
                await self.response.defer(thinking=True, ephemeral=True)
            await self.commit(
                self.interaction, effect.kind, effect.payload, self.session
            )
        elif isinstance(effect, RunAside):
            await self.aside(self.interaction, effect.name, self.session)
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

    # ------------------------------------------------------------ transport

    @property
    def response(self) -> Any:
        """The interaction's first answer."""
        return self.interaction.response

    @property
    def followup(self) -> Any:
        """The interaction's webhook, for everything after the first answer."""
        return self.interaction.followup

    def _ids(self) -> renderer.Ids:
        return renderer.Ids(self.session.id, self.session.revision, self.session.cursor)

    async def ack(self) -> None:
        """Acknowledge without changing the screen."""
        if not self.response.is_done():
            await self.response.defer()

    def _on_own_message(self) -> bool:
        message = self.interaction.message
        return message is not None and message.id == self.surface.message_id

    async def _send(self, **payload: Any) -> Any:
        if not self.response.is_done():
            await self.response.send_message(ephemeral=True, **payload)
            return await self.interaction.original_response()
        return await self.followup.send(ephemeral=True, **payload)

    async def _edit(self, **payload: Any) -> None:
        if self._on_own_message() and not self.response.is_done():
            await self.response.edit_message(**payload)
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
            layout = renderer.layout_of(screen, self._ids(), self.dispatcher)
            self.surface.view, self.surface.embed = layout, None
            return {"view": layout}
        embed = renderer.embed_of(screen)
        view = renderer.view_of(screen, self._ids(), self.dispatcher)
        self.surface.view, self.surface.embed = view, embed
        return {"embed": embed, "view": view}

    async def render(self, screen: Screen) -> None:
        """First send, then edit in place, and replace when the flavour changes."""
        payload = self._payload(screen)
        if self.surface.message_id is None:
            self._own(await self._send(**payload))
        elif screen.is_layout != self.surface.is_layout or self.surface.replace_next:
            await self._replace(**payload)
        else:
            await self._edit(**payload)
        self.surface.is_layout = screen.is_layout
        self.surface.replace_next = False

    async def open_modal(self, effect: OpenModal) -> None:
        """Open the modal as the interaction's first answer."""
        modal = renderer.modal_of(
            effect.screen, self._ids(), self.dispatcher, effect.action, effect.arg
        )
        await self.response.send_modal(modal)

    async def show_error(self, effect: ShowError) -> None:
        """An ephemeral error, as an embed or a plain sentence."""
        payload: dict[str, Any]
        if effect.plain:
            key = effect.key if "." in effect.key else f"errors.{effect.key}.message"
            content = text(key, self.locale)
            for name, value in effect.args.items():
                content = content.replace("{" + name + "}", value)
            payload = {"content": content}
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
        view = renderer.view_of(screen, self._ids(), self.dispatcher)
        message = await self.followup.send(
            embed=renderer.embed_of(screen), view=view, ephemeral=True
        )
        self.surface.confirmation_id = message.id

    async def dismiss(self) -> None:
        """Remove the confirmation the interaction came from."""
        await self.ack()
        await self.interaction.delete_original_response()
        self.surface.confirmation_id = None

    # ------------------------------------------------------------- finalize

    async def _strip_controls(self) -> None:
        if self.surface.is_layout and self.surface.view is not None:
            await self._edit(view=renderer.finalized_layout(self.surface.view))
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
        embed = discord.Embed(color=int(Style.BACKGROUND_COLOR, base=16))
        footer = text("commands.commands.commons.embed.footer", self.locale)
        if footer:
            embed.set_footer(text=f"• {footer}")
        return embed

    async def finalize(self, kind: str) -> None:
        """The final state of the session, controls gone."""
        if kind == "discarded":
            await self._strip_controls()
            if self.surface.confirmation_id is not None:
                await self.interaction.delete_original_response()
            return
        if kind == "expired":
            await self._expire()
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
            return embed, False
        if kind == "edited":
            base = manage.panel_embed(self.session_definition(), self.locale)
            embed = renderer.embed_of(base)
            embed.title, embed.description = title, description
            embed.set_thumbnail(url=KeikoIcons.IMAGE_02)
            return embed, True
        if kind in ("added", "removed"):
            embed = self.surface.embed or discord.Embed(
                color=int(Style.BACKGROUND_COLOR, base=16)
            )
            embed.clear_fields()
            footer = text("commands.commands.commons.embed.footer", self.locale)
            if footer:
                embed.set_footer(text=f"• {footer}")
            embed.set_thumbnail(url=KeikoIcons.IMAGE_02)
            embed.title, embed.description = title, description
            return embed, self.surface.is_layout
        embed = self._fresh_embed()
        embed.title, embed.description = title, description
        return embed, True

    def session_definition(self) -> Any:
        """The definition the session runs, from the registry."""
        from app.forms.definitions.registry import registry

        return registry.get(*self.session.definition)

    async def _final(self, embed: discord.Embed, replace: bool) -> None:
        if replace or self.surface.message_id is None:
            await self._replace(embed=embed, view=None)
        else:
            await self._edit(embed=embed, view=None)
        self.surface.is_layout = False
        self.surface.view, self.surface.embed = None, embed

    async def _expire(self) -> None:
        await self._edit(**expired_payload(self.surface, self.locale))
        self.surface.view = None


def expired_payload(surface: Surface, locale: str) -> dict[str, Any]:
    """The message as it looks once its session expired: no controls, a notice."""
    notice = text("commands.form-notices.expired", locale)
    if surface.is_layout and surface.view is not None:
        view = renderer.finalized_layout(surface.view)
        container = cast(Any, view.children[0]) if view.children else None
        if container is not None:
            container.add_item(discord.ui.TextDisplay(notice))
        return {"view": view}
    embed = surface.embed or base_embed("", "")
    embed.description = f"{embed.description or ''}\n\n{notice}".strip()
    return {"embed": embed, "view": None}


async def expire_surface(surface: Surface, locale: str) -> None:
    """Close the message of a session that expired with nobody clicking."""
    if surface.message is None or surface.view is None:
        return
    try:
        await surface.message.edit(**expired_payload(surface, locale))
    except discord.HTTPException as error:
        logger.warn(
            f"Could not expire a form message: {type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
    surface.view = None


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
