"""FormScenario: drives real Keiko form flows offline, on the form platform.

Entry points are the production seams every cog funnels through: the
adapter's `open_feature` (a slash command, a `/setup` or a greeting button).
Everything downstream — YAML definitions, the engine, the renderer, i18n,
features, the data layer — is real; only the Discord transport
(FakeInteraction) and Mongo/Redis (existing mocks) are fake.

Tests drive it through its public actions and reads only (the table in
`docs/form-scenario-testing.md`); what a test cannot say with them is a new
action here, never a reach into the store, the runtime or discord.py.
"""
import asyncio
import contextlib
import copy
import datetime
import time
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional, Sequence, Union

import discord

from app.data import data, wait
from app.forms.discord import callbacks, transitions
from app.forms.discord.callbacks import RUNTIME
from app.forms.discord.interactions import decode as decode_component_id
from app.forms.form.form_state import Status
from tests.behavioral.harness import locators
from app.core.commands import setup_features
from tests.generators.moderations import recorded_server, seed_cog
from tests.behavioral.harness.errors import (
    LocatorError,
    ScenarioAssertionError,
)
from tests.behavioral.harness.fake_interaction import FakeInteraction
from tests.behavioral.harness.message_store import FakeMessage, MessageStore
from tests.behavioral.harness.normalizer import is_session_coded
from tests.behavioral.harness.transcript import format_transcript

_LOCALES = {
    "pt-br": discord.Locale.brazil_portuguese,
    "en-us": discord.Locale.american_english,
}

_CONTENT_KINDS = ("send", "edit", "followup_send", "followup_edit", "edit_original")
_SELECTS = (
    discord.ui.Select, discord.ui.ChannelSelect, discord.ui.RoleSelect,
    discord.ui.UserSelect, discord.ui.MentionableSelect,
)
# A deferred interaction answers with a followup: same message, other HTTP call.
_ANSWERED_AS = {"followup_send": "send", "followup_edit": "edit"}


FEATURE_KEYS = {spec.key for spec in setup_features()}


class FormScenario:
    def __init__(self, *, guild, user, locale: str, mongo=None, tree=None):
        self.locale_str = locale
        self.locale = _LOCALES[locale]
        self.guild = guild
        self.user = user
        self.db = mongo
        if mongo is not None:
            recorded_server(mongo, guild.id, language=locale)
        self.store = MessageStore()
        # The app command tree Keiko's client holds (`app_command_tree`), read by the help.
        self.tree = tree
        self.command_key: Optional[str] = None
        # How long each first answer takes to reach Discord (FakeResponse).
        self.latency = 0.0
        # What the server had saved when the admin first opened a command.
        self.saved_at_open: Optional[List[Dict[str, Any]]] = None
        # The caller's card a command opens from (`open_from`), and its message.
        self._caller: Optional[tuple] = None
        self.launcher: Optional[FakeMessage] = None
        # The bot every interaction reaches (`interaction.client`), when a test gives one.
        self.client: Any = None

    # ------------------------------------------------------------------ setup

    async def start(self, command_key: str) -> "FormScenario":
        """Open the command with nothing saved: the setup form."""
        self.command_key = command_key
        self.store.record("start", actor="user", target=command_key,
                          values=self.locale_str)
        await self._open_feature(self.interaction(), command_key)
        return self

    async def start_manager(self, command_key: str, cog_data: Dict[str, Any]) -> "FormScenario":
        """Open the command over a saved document: the manager panel."""
        self.command_key = command_key
        self.store.record("start_manager", actor="user", target=command_key,
                          values=self.locale_str)
        self._seed_document(command_key, cog_data)
        await self._open_feature(self.interaction(), command_key)
        return self

    async def start_command(self, command_key: str) -> "FormScenario":
        """Open the command the way a slash command or a /setup button does:
        the platform routes to the setup form or the manager from what is
        saved (seed Mongo first)."""
        self.command_key = command_key
        self.store.record("start_command", actor="user", target=command_key,
                          values=self.locale_str)
        await self._open_feature(self.interaction(), command_key)
        return self

    def open_from(self, card: discord.ui.View, *, source: str,
                  ending=None, back=None) -> "FormScenario":
        """Open every later command from a button on `card`, a caller's screen
        (a /setup row): the card is sent first and becomes `launcher`, then the
        feature opens from a click on it with `source`, `ending` and `back` as
        that caller passes them."""
        self._caller = (card, source, ending, back)
        return self

    async def run(self, entry, *args, name: Optional[str] = None) -> "FormScenario":
        """Reach Keiko through an entry point the scenario does not open itself
        (`/configurar`, a greeting): `entry(interaction, *args)` is awaited with
        a fresh interaction of the admin, as Discord would send it."""
        self.store.record("start", actor="user",
                          target=name or getattr(entry, "__name__", repr(entry)))
        await entry(self.interaction(), *args)
        self.store.step_provider = self._current_step_key
        return self

    async def run_command(self, command, binding, *args) -> FakeInteraction:
        """A member runs the app command `command` of `binding` (its cog), as
        discord.py runs it: the interaction carries the command and passes
        Keiko's tree check (the server's language spoken from then on, the
        member's own kept as the reader's), then the callback gets `args`.
        Returns the interaction sent."""
        from tests.mocks.discord import the_tree_checks

        self.store.record("command", actor="user", target=f"/{command.qualified_name}")
        interaction = self.interaction()
        interaction.command = command
        interaction.extras = {}
        await the_tree_checks(interaction)
        await command.callback(binding, interaction, *args)
        self.store.step_provider = self._current_step_key
        return interaction

    def post(self, view: discord.ui.View, *, embed=None) -> FakeMessage:
        """The bot posts `view` outside any command (the greeting it sends on
        joining a server): the message is on screen and the admin may click it."""
        message = self.store.create([embed] if embed else [], view, False)
        self.store.record("channel_send", message=message.id, embeds=message.embeds,
                          view=view, ephemeral=False)
        return message

    async def _open_feature(self, interaction, command_key: str) -> None:
        if self.saved_at_open is None and self.db is not None:
            self.saved_at_open = copy.deepcopy(list(self.db.guild.cogs.find({})))
        if self._caller is None:
            await RUNTIME.open_feature(interaction, command_key)
        else:
            card, source, ending, back = self._caller
            self.launcher = self.store.create([], card, True)
            self.store.record("send", message=self.launcher.id, view=card,
                              ephemeral=True)
            await RUNTIME.open_feature(
                self.interaction(on=self.launcher), command_key, source, ending, back
            )
        self.store.step_provider = self._current_step_key

    def _seed_document(self, command_key: str, cog_data: Dict[str, Any]) -> None:
        """What the old manager received as an argument, the platform reads from Mongo."""
        document = copy.deepcopy(cog_data)
        document["guild_id"] = str(self.guild.id)
        document.setdefault("enabled", True)
        if self.db.guild.cogs.find_one({"_id": f"{self.guild.id}:{command_key}"}) is None:
            seed_cog(self.db, command_key, document)

    # ------------------------------------------------------------ user actions

    def interaction(self, *, on: Optional[FakeMessage] = None,
                    target: Union[str, discord.ui.Item, None] = None,
                    at: Optional[datetime.datetime] = None) -> FakeInteraction:
        """The interaction Discord sends for one action of the admin, not sent yet.

        With nothing given it is a slash command's (no message); `on` is the
        message clicked and `target` what is clicked on it (a target as `click`
        reads it, or a component from `component`); `at` is when Discord
        created it. Hand it to `click(..., interaction=)` to deliver the same
        click twice, or to an entry point the scenario does not open itself."""
        custom_id = None
        if target is not None:
            message = on or self._require_message()
            custom_id = self._custom_id(self._component(message, target))
        interaction = FakeInteraction(
            self.store, guild=self.guild, user=self.user, locale=self.locale,
            message=on, data={"custom_id": custom_id} if custom_id else {},
            latency=self.latency, tree=self.tree,
        )
        if self.client is not None:
            interaction.client = self.client
        if at is not None:
            interaction.created_at = at
        return interaction

    def component(self, target: str, *, on: Optional[FakeMessage] = None):
        """The button or select `target` names on `on` (default: the current
        message), as the admin sees it now.

        A click on it later still presses this very component, even after the
        message was drawn again: a screen the admin has not refreshed yet."""
        return self._component(on or self._require_message(), target)

    async def click(self, target: Union[str, discord.ui.Item], *,
                    on: Optional[FakeMessage] = None,
                    at: Optional[datetime.datetime] = None,
                    interaction: Optional[FakeInteraction] = None) -> None:
        """Press `target` on `on` (default: the current message).

        `target` is a locator target (the table in docs/form-scenario-testing.md)
        or a component from `component`; `at` dates the click; `interaction`
        delivers one built by `interaction()` (the same click twice, two racing)."""
        message = on or self._require_message()
        button = self._component(message, target)
        custom_id = self._custom_id(button)
        self.store.record("click", actor="user", message=message.id,
                          target=self._described(target),
                          custom_id=None if is_session_coded(custom_id) else custom_id)
        if interaction is None:
            interaction = self.interaction(on=message, target=button, at=at)
        await locators.dispatch_click(message.view, button, interaction)

    def restart(self) -> None:
        """The bot restarts: every open session is forgotten, and no view
        discord.py held survives, so a later click on a message drawn before
        reaches the bot only through `click_after_restart`."""
        self.store.record("restart", actor="clock")
        RUNTIME.reset()

    async def click_after_restart(
        self, target: Union[str, discord.ui.Item], *,
        on: Optional[FakeMessage] = None,
        interaction: Optional[FakeInteraction] = None,
    ) -> FakeInteraction:
        """A click that lands when no view holds its message (after `restart`, or
        once the view timed out with its session), routed as discord.py routes
        it: to every dynamic item the bot registers whose template matches the
        custom id, rebuilt from the message. Returns the interaction sent."""
        message = on or self._require_message()
        item = self._component(message, target)
        custom_id = self._custom_id(item) or ""
        self.store.record("click_after_restart", actor="user", message=message.id,
                          target=self._described(target))
        if interaction is None:
            interaction = self.interaction(on=message, target=item)
        if _routes(custom_id):
            layout = message.flags.components_v2
            view_type = discord.ui.LayoutView if layout else discord.ui.View
            rebuilt = view_type.from_message(message, timeout=None)
            base = next(
                found for found in rebuilt.walk_children()
                if getattr(found, "custom_id", None) == custom_id
            )
            await _route(interaction, custom_id, base)
        return interaction

    async def click_custom_id(self, custom_id: str, *,
                              on: Optional[FakeMessage] = None) -> FakeInteraction:
        """A click Discord delivers by its custom id alone to a bot that holds no
        view for it (a control drawn before a restart, or before a release that
        removed its feature), routed through the dynamic items as discord.py
        routes it; `on` is the message it sits on, None when there is none to
        edit. Returns the interaction sent."""
        self.store.record("click_custom_id", actor="user",
                          message=on.id if on is not None else None)
        interaction = self.interaction(on=on)
        interaction.data = {"custom_id": custom_id}
        await _route(interaction, custom_id, discord.ui.Button(custom_id=custom_id))
        return interaction

    async def submit_modal_after_restart(self) -> FakeInteraction:
        """The pending modal submitted when no view holds its form: discord.py
        knows no modal for it, so the submit reaches the bot's interaction
        listener (`Events.on_interaction`). Returns the interaction sent."""
        from app.core.events import Events

        modal = self.store.pending_modal
        if modal is None:
            raise LocatorError("no modal is pending", self.transcript)
        self.store.pending_modal = None
        self.store.record("modal_submit_after_restart", actor="user")
        interaction = self.interaction(on=self._opened_from(modal))
        interaction.data = {"custom_id": modal.custom_id}
        interaction.type = discord.InteractionType.modal_submit
        await Events(SimpleNamespace()).on_interaction(interaction)
        return interaction

    async def select_option(self, values: Union[Any, Sequence[Any]], *,
                            target: Optional[str] = None) -> None:
        message = self._require_message()
        select = locators.find_select(message, target)
        if select.view is None:
            raise LocatorError(
                f"View interaction referencing unknown view for item {select!r}. "
                "Discarding — the message still shows this select, but its view "
                "was cleared or replaced without editing the message.",
                self.transcript,
            )
        if not isinstance(values, (list, tuple)):
            values = [values]
        resolved = [self._resolve_select_value(select, value) for value in values]
        select._values = resolved
        self.store.record("select", actor="user", message=message.id,
                          target=target or select.placeholder,
                          values=[self._display_value(v) for v in resolved])
        await select.callback(self.interaction(on=message))

    async def submit_modal(self, fields: Dict[str, str], *, modal=None) -> None:
        """Submit the pending modal with `fields`, each named by its label
        (exact, then contains): a text input gets the text, a select the
        option whose label or value it names, a file upload a small PNG named
        by it (`fake_attachment`). `modal` submits one the user opened earlier
        and still holds on another screen (a later one replaced it as pending).
        What `on_submit` raises reaches the modal's own `on_error`, as discord.py
        routes it; a modal that keeps discord.py's default raises it here, so a
        failure never passes for a submit."""
        modal = modal or self.store.pending_modal
        if modal is None:
            raise LocatorError("no modal is pending", self.transcript)
        labelled = self._modal_fields(modal)
        for key, value in fields.items():
            self._fill(self._match_input(labelled, key), value)
        if self.store.pending_modal is modal:
            self.store.pending_modal = None
        self.store.record("modal_submit", actor="user",
                          fields={k: v for k, v in fields.items()})
        interaction = self.interaction(on=self._opened_from(modal))
        try:
            await modal.on_submit(interaction)
        except Exception as error:
            if type(modal).on_error is discord.ui.Modal.on_error:
                raise
            await modal.on_error(interaction, error)

    def _opened_from(self, modal: discord.ui.Modal) -> Optional[FakeMessage]:
        opened = self.store.modal_messages.get(id(modal))
        if opened is None or opened.deleted:
            return self.current_message
        return opened

    def dismiss_modal(self) -> None:
        """The user closes the modal without submitting (Esc / clicking away).
        Discord tells the bot NOTHING when this happens: no interaction, no
        event. The message that opened the modal stays on screen unchanged."""
        if self.store.pending_modal is None:
            raise LocatorError("no modal is pending", self.transcript)
        self.store.pending_modal = None
        self.store.record("modal_dismiss", actor="user")

    def pending_modal_fields(self) -> List[str]:
        """The label of every input of the pending modal, in order."""
        return [label for label, _item in self._pending_fields()]

    def pending_modal_inputs(self) -> List[Any]:
        """The inputs of the pending modal in the order the user sees them: its
        text inputs, selects and file uploads."""
        return [item for _label, item in self._pending_fields()]

    def dismiss(self, message: Optional[FakeMessage] = None) -> None:
        """The admin closes an ephemeral message (Discord's "Dismiss message"),
        by default the current one: it leaves the screen and the bot is told
        nothing."""
        message = message or self._require_message()
        message.deleted = True
        self.store.record("dismiss", actor="user", message=message.id)

    async def submit_file_upload(self, *, filename: str = "image.png",
                                 content: bytes = b"fake-image-bytes",
                                 size: Optional[int] = None,
                                 width: Optional[int] = 800,
                                 height: Optional[int] = 400,
                                 content_type: Optional[str] = "image/png") -> None:
        """Submit a pending file-upload modal with a fake attachment: by default a
        small PNG, measured the way Discord describes an attachment."""
        modal = self.store.pending_modal
        if modal is None or getattr(modal, "file_upload", None) is None:
            raise LocatorError("no file-upload modal is pending", self.transcript)

        attachment = fake_attachment(
            filename, content=content, size=size, width=width, height=height,
            content_type=content_type,
        )
        modal.file_upload._values = [attachment]
        self.store.pending_modal = None
        self.store.record("modal_submit", actor="user", fields={"file": filename})
        await modal.on_submit(self.interaction(on=self._opened_from(modal)))

    async def confirm(self) -> None:
        await self.click("continue")

    async def cancel(self) -> None:
        await self.click("cancel")

    async def go_back(self) -> None:
        await self.click("back")

    async def expire(self) -> None:
        """The clock passes every open session's deadline with nobody clicking."""
        self.store.record("expire", actor="clock")
        await RUNTIME.expire_stale(datetime.datetime.max.replace(tzinfo=datetime.timezone.utc))

    async def sweep(self) -> None:
        """The expiry loop runs once with the clock past every deadline: what
        nobody finished expires, and what already ended is forgotten."""
        self.store.record("sweep", actor="clock")
        await RUNTIME.sweep(datetime.datetime.max.replace(tzinfo=datetime.timezone.utc))

    async def expiry_loop_pass(self) -> None:
        """One pass of the bot's expiry loop (`Events.sweep_forms`) a session's
        lifetime from now (from the clock `later` sets): what nobody touched since
        is past its deadline and expires, what ended is forgotten, and a pass that
        fails is logged, never raised. Like `sweep`, it takes Discord's token for
        the latest click as still alive."""
        from app.constants import ViewConstants
        from app.core.events import Events

        self.store.record("expiry_loop_pass", actor="clock")
        cog = Events(SimpleNamespace())
        now = callbacks._now
        lifetime = datetime.timedelta(seconds=ViewConstants.LONG_TIMEOUT_SECONDS + 1)
        callbacks._now = lambda: now() + lifetime
        try:
            await type(cog).sweep_forms.coro(cog)
        finally:
            callbacks._now = now

    @contextlib.contextmanager
    def later(self, seconds: float) -> Iterator[None]:
        """The clock reads `seconds` later for what runs inside: the deadline a
        session counts from, the fifteen minutes Discord honours an interaction
        token, a cooldown, the expiry loop's pass."""
        self.store.record("later", actor="clock", values=seconds)
        real_now = callbacks._now
        clocks = {module: module.time for module in (callbacks, transitions)}
        shifted = SimpleNamespace(
            **{name: getattr(time, name) for name in dir(time) if not name.startswith("_")}
        )
        shifted.monotonic = lambda: time.monotonic() + seconds
        callbacks._now = lambda: real_now() + datetime.timedelta(seconds=seconds)
        for module in clocks:
            module.time = shifted
        try:
            yield
        finally:
            callbacks._now = real_now
            for module, clock in clocks.items():
                module.time = clock

    def discord_refuses(self, api: str, error: Optional[Exception] = None, *,
                        times: Optional[int] = None) -> None:
        """Discord answers the next `times` calls of `api` (every one when None)
        with `error` (default: a 500) instead of doing them. `api` names the call
        as the bot makes it: `response.send_message`, `response.defer`,
        `response.edit_message`, `response.send_modal`, `followup.send`,
        `followup.edit_message`, `followup.delete_message`,
        `edit_original_response`, `delete_original_response`."""
        if error is None:
            error = discord.HTTPException(
                SimpleNamespace(status=500, reason="Internal Server Error"),
                "Internal Server Error",
            )
        self.store.refuse(api, error, times)

    async def finish(self) -> None:
        """Let any task the flow scheduled settle."""
        await asyncio.sleep(0)

    # ------------------------------------------------------------- assertions

    def expect_step(self, key: str) -> None:
        actual = self._current_step_key() or "form"
        if actual != key:
            self._fail(f"expected step {key!r}, form is on step {actual!r}")

    def expect_message(self, *, kind: Optional[str] = None,
                       title_contains: Optional[str] = None,
                       description_contains: Optional[str] = None,
                       content_contains: Optional[str] = None,
                       has_component: Optional[str] = None,
                       ephemeral: Optional[bool] = None,
                       components_v2: Optional[bool] = None) -> Dict[str, Any]:
        event = self._last_content_event()
        embed = event.get("embed") or {}
        if kind is not None and _ANSWERED_AS.get(event["kind"], event["kind"]) != kind:
            self._fail(f"expected kind {kind!r}, last message event is {event['kind']!r}")
        if title_contains is not None and title_contains not in (embed.get("title") or ""):
            self._fail(f"title {embed.get('title')!r} does not contain {title_contains!r}")
        if description_contains is not None and \
                description_contains not in (embed.get("description") or ""):
            self._fail(
                f"description does not contain {description_contains!r} "
                f"(got {embed.get('description')!r})"
            )
        if content_contains is not None and \
                content_contains not in (event.get("content") or ""):
            self._fail(f"content does not contain {content_contains!r}")
        if ephemeral is not None and bool(event.get("ephemeral")) != ephemeral:
            self._fail(f"expected ephemeral={ephemeral}, event says {event.get('ephemeral')}")
        if components_v2 is not None and bool(event.get("components_v2")) != components_v2:
            self._fail(f"expected components_v2={components_v2}")
        if has_component is not None:
            self.expect_component(label_or_action=has_component)
        return event

    def expect_component(self, *, label_or_action: str,
                         disabled: Optional[bool] = None) -> None:
        message = self._require_message()
        try:
            button = locators.find_button(message, label_or_action, self.locale)
        except LocatorError:
            self._fail(f"component {label_or_action!r} not on current message")
            return
        if disabled is not None and button.disabled != disabled:
            self._fail(f"component {label_or_action!r} disabled={button.disabled}")

    def expect_status(self, status: Union[Status, str],
                      session: Optional[str] = None) -> None:
        """The session `session` (an id read from `scenario.session.id` while it
        was on screen; default: the one on screen now) stands as `status`
        (`Status` or its value: "active", "awaiting", "cancelled"...)."""
        wanted = Status(status)
        session_id = session if session is not None else getattr(self.session, "id", None)
        found = RUNTIME.store.get(session_id) if session_id is not None else None
        if found is None:
            self._fail(f"no session {session_id!r} in the runtime (never opened, or forgotten)")
            return
        if found.status is not wanted:
            self._fail(f"session {session_id!r} is {found.status.value!r}, "
                       f"expected {wanted.value!r}")

    def expect_mode(self, kind: str, key: Optional[str] = None) -> None:
        """The session in front of the admin runs as `kind`: "setup", "manage"
        (the panel), "edit", "add_item", "edit_item" or "remove_item", of the
        feature `key` when given."""
        session = self.session
        if session is None:
            self._fail(f"expected a session in mode {kind!r}, none is on screen")
            return
        if session.mode.kind != kind:
            self._fail(f"the session on screen runs as {session.mode.kind!r}, "
                       f"expected {kind!r}")
        if key is not None and session.key != key:
            self._fail(f"the session on screen is {session.key!r}, expected {key!r}")

    def expect_error(self, message_contains: str) -> None:
        """The admin reads the error in the last error message sent."""
        for event in reversed(self.store.events):
            embed = event.get("embed") or {}
            is_error_embed = embed.get("color") == 0xFF0000
            has_content = bool(event.get("content"))
            if event["actor"] == "bot" and (is_error_embed or has_content):
                haystack = " ".join(filter(None, [
                    event.get("content"), embed.get("title"), embed.get("description"),
                ]))
                if message_contains in haystack:
                    return
                self._fail(
                    f"last error/content message does not contain "
                    f"{message_contains!r} (got {haystack!r})"
                )
                return
        self._fail(f"no error message found containing {message_contains!r}")

    def expect_modal(self, *, title_contains: Optional[str] = None,
                     field_labels: Optional[List[str]] = None) -> None:
        modal = self.store.pending_modal
        if modal is None:
            self._fail("expected a pending modal, none was sent")
            return
        if title_contains is not None and title_contains not in (modal.title or ""):
            self._fail(f"modal title {modal.title!r} does not contain {title_contains!r}")
        if field_labels is not None:
            labels = [label for label, _item in self._modal_fields(modal)]
            missing = [label for label in field_labels if label not in labels]
            if missing:
                self._fail(f"modal is missing fields {missing} (has {labels})")

    @property
    def rendered_summary(self) -> str:
        """Everything the user reads on the panel, wherever the design put it:
        the embed description, its fields, and — for a Components V2 panel,
        which carries no embed at all — the container's text displays."""
        embed = self._last_content_event().get("embed") or {}
        parts = [embed.get("description") or ""]
        parts += [
            f"{field.get('name')}\n{field.get('value')}"
            for field in embed.get("fields") or []
        ]
        message = self.current_message
        if message is not None:
            parts += [
                item.content or ""
                for item in locators.walk_items(message.view)
                if isinstance(item, discord.ui.TextDisplay)
            ]
        return "\n".join(part for part in parts if part)

    def expect_configuration_values(self, *values: str) -> None:
        """Assert saved configuration values appear in the manager summary."""
        summary = self.rendered_summary
        missing = [value for value in values if value not in summary]
        if missing:
            self._fail(
                f"manager summary is missing {missing!r}.\nRendered summary:\n"
                f"{summary}"
            )

    def expect_persisted(self, database: str, collection: str,
                         filter_subset: Dict[str, Any],
                         doc_subset: Dict[str, Any]) -> Dict[str, Any]:
        document = self.get_persisted(database, collection, filter_subset)
        if document is None:
            self._fail(
                f"no document in {database}.{collection} matching {filter_subset!r}"
            )
        mismatches = {
            key: (value, document.get(key))
            for key, value in doc_subset.items()
            if document.get(key) != value
        }
        if mismatches:
            self._fail(
                f"document in {database}.{collection} does not match: "
                + "; ".join(
                    f"{key}: expected {exp!r}, got {got!r}"
                    for key, (exp, got) in mismatches.items()
                )
            )
        return document

    def expect_not_persisted(self, database: str, collection: str,
                             filter_subset: Dict[str, Any]) -> None:
        if self.get_persisted(database, collection, filter_subset) is not None:
            self._fail(
                f"expected NO document in {database}.{collection} "
                f"matching {filter_subset!r}, found one"
            )

    def get_persisted(self, database: str, collection: str,
                      filter_subset: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if database == "guild" and collection in FEATURE_KEYS:
            return next(
                (
                    document
                    for document in wait(data().guild_cogs.find_all_cogs(collection))
                    if all(document.get(key) == value for key, value in filter_subset.items())
                ),
                None,
            )
        return self.db[database][collection].find_one(filter_subset)

    # ------------------------------------------------------------ introspection

    @property
    def outputs(self) -> List[Dict[str, Any]]:
        return self.store.events

    @property
    def transcript(self) -> str:
        return format_transcript(self.store.events)

    @property
    def current_message(self) -> Optional[FakeMessage]:
        return self.store.current

    @property
    def live_messages(self) -> List[FakeMessage]:
        """Every message still on screen, deleted and dismissed ones left out,
        oldest first."""
        return [message for message in self.store.messages.values() if not message.deleted]

    def alias(self, message: FakeMessage) -> str:
        """The name the transcript and `outputs` give `message`: M1, M2..."""
        return self.store.alias(message.id)

    @property
    def session(self):
        """The engine session in front of the admin: the one behind the current
        message, or the child it opened when that child shows a modal."""
        message = self.current_message
        session = None
        for item in locators.clickable_items(message) if message else []:
            component = decode_component_id(getattr(item, "custom_id", None) or "")
            if component is not None:
                session = RUNTIME.store.get(component.session_id)
                break
        while session is not None and session.awaiting == "child":
            children = [c for c in RUNTIME.store.children(session.id) if not c.is_closed]
            if not children:
                break
            session = children[-1]
        return session

    @property
    def pending_modal(self) -> Optional[discord.ui.Modal]:
        """The modal waiting for the admin's submit, None when none is open."""
        return self.store.pending_modal

    @property
    def sessions(self) -> List[Any]:
        """Every session the runtime still holds, open or ended, oldest first."""
        return list(RUNTIME.store._sessions.values())

    @property
    def held_sessions(self) -> List[str]:
        """The id of every session the bot still keeps anything for: in the
        engine's store or in what the adapter holds beside it (the message, the
        lock, the previews). A session one half forgot and the other kept leaks."""
        return sorted(set(RUNTIME.store._sessions) | set(RUNTIME.sessions))

    async def fill_tokens(self, text: str) -> str:
        """`text` with its `${...}` tokens filled as the form on screen fills its
        own copy: its answers and its context (the server's name, prefix,
        language and time zone)."""
        from app.forms.form.actions.action import apply_tokens

        session = self.session
        if session is None:
            self._fail("no form on screen to fill tokens for")
        return apply_tokens(text, session, await RUNTIME._context(session))

    @property
    def card_state(self) -> Dict[str, Any]:
        """The state the card on screen is drawn from (defaults included)."""
        from app.forms.form.form_yaml import registry
        from app.forms.form.form_yaml import CardStep
        from app.forms.form.form import steps_for
        from app.core.conditions import Scope
        from app.forms.form.actions.configuration_card import state_of
        from app.forms.form.actions.action import Context

        session = self.session
        if session is None:
            return {}
        definition = registry.get(*session.definition)
        steps = steps_for(definition, session.mode)
        step = next((s for s in steps if s.key == session.cursor), None)
        if not isinstance(step, CardStep):
            return {}
        state = RUNTIME.sessions.get(session.id)
        document = state.opened.document if state and state.opened.document else {}
        context = Context(
            definition=definition,
            steps=steps,
            locale=self.locale_str,
            scope=Scope(session.values()),
            document=document,
        )
        return state_of(step, session, context)

    @property
    def answers(self) -> Dict[str, Any]:
        """The raw answers of the session on screen, by key."""
        session = self.session
        return dict(session.values()) if session is not None else {}

    # ---------------------------------------------------------------- internal

    def _component(self, message: FakeMessage, target: Union[str, discord.ui.Item]):
        if isinstance(target, discord.ui.Item):
            return target
        try:
            return locators.find_button(message, target, self.locale, self._section_key)
        except LocatorError as missing:
            selects = [i for i in locators.clickable_items(message) if isinstance(i, _SELECTS)]
            if not selects or locators.section_target(target) is not None:
                raise LocatorError(str(missing), self.transcript) from None
            try:
                return locators.find_select(message, target)
            except LocatorError:
                raise LocatorError(str(missing), self.transcript) from None

    @staticmethod
    def _custom_id(item) -> Optional[str]:
        if isinstance(item, discord.ui.DynamicItem):
            return item.custom_id
        return item.custom_id if getattr(item, "_provided_custom_id", False) else None

    @staticmethod
    def _described(target: Union[str, discord.ui.Item]) -> str:
        if isinstance(target, str):
            return target
        return locators.codec_target(target) or getattr(target, "label", None) or repr(target)

    def _section_key(self, item) -> Optional[str]:
        """The key of the card section a section button opens or resets: the
        section at that index of the card the button's session is on, or the
        step itself for a refused modal step drawn as a one-field card."""
        from app.forms.form.form import steps_for
        from app.forms.form.form_yaml import CardStep, registry

        component = decode_component_id(getattr(item, "custom_id", None) or "")
        if component is None or not (component.arg or "").isdigit():
            return None
        session = RUNTIME.store.get(component.session_id)
        if session is None:
            return None
        definition = registry.get(*session.definition)
        steps = steps_for(definition, session.mode)
        step = next((s for s in steps if s.key == session.cursor), None)
        if isinstance(step, CardStep):
            index = int(component.arg)
            return step.sections[index].key if index < len(step.sections) else None
        return step.key if step is not None else None

    def _require_message(self) -> FakeMessage:
        message = self.current_message
        if message is None or message.view is None:
            raise LocatorError("no live message with components", self.transcript)
        return message

    def _current_step_key(self) -> Optional[str]:
        session = self.session
        return session.cursor if session is not None else None

    def _last_content_event(self) -> Dict[str, Any]:
        for event in reversed(self.store.events):
            if event["actor"] == "bot" and event["kind"] in _CONTENT_KINDS:
                return event
        self._fail("no bot message event recorded yet")

    @staticmethod
    def _modal_fields(modal) -> List[tuple]:
        """(label, input) of every input of `modal`: a `discord.ui.Label` names
        the input it wraps, a bare text input carries its own label."""
        fields = []
        for child in modal.children:
            if isinstance(child, discord.ui.Label):
                fields.append((child.text, child.component))
            elif isinstance(child, discord.ui.TextInput):
                fields.append((child.label, child))
        return fields

    def _pending_fields(self) -> List[tuple]:
        modal = self.store.pending_modal
        if modal is None:
            raise LocatorError("no modal is pending", self.transcript)
        return self._modal_fields(modal)

    def _match_input(self, fields: List[tuple], key: str):
        for label, item in fields:
            if (label or "").lower() == key.lower():
                return item
        for label, item in fields:
            if key.lower() in (label or "").lower():
                return item
        raise LocatorError(
            f"modal has no field matching {key!r} "
            f"(fields: {[label for label, _item in fields]})",
            self.transcript,
        )

    def _fill(self, item, value: str) -> None:
        if isinstance(item, discord.ui.TextInput):
            item._value = value
        elif isinstance(item, discord.ui.Select):
            option = next(
                (o for o in item.options if value in (o.label, o.value)), None
            )
            if option is None:
                raise LocatorError(
                    f"the select offers no option {value!r} "
                    f"(options: {[o.label for o in item.options]})",
                    self.transcript,
                )
            item._values = [option.value]
        elif isinstance(item, discord.ui.FileUpload):
            item._values = [fake_attachment(value)]
        else:
            raise LocatorError(f"cannot fill {type(item).__name__}", self.transcript)

    def _resolve_select_value(self, select, value: Any):
        if not isinstance(value, str):
            return value
        if isinstance(select, discord.ui.RoleSelect):
            pool, kind = self.guild.roles, "role"
        elif isinstance(select, discord.ui.ChannelSelect):
            pool, kind = self.guild.text_channels, "channel"
        elif isinstance(select, discord.ui.UserSelect):
            pool, kind = self.guild.members, "member"
        else:
            return value
        for candidate in pool:
            if candidate.name == value or str(candidate.id) == value:
                return candidate
        raise LocatorError(
            f"no {kind} named {value!r} in the mock guild "
            f"(available: {[getattr(c, 'name', c) for c in pool]})",
            self.transcript,
        )

    @staticmethod
    def _display_value(value: Any) -> str:
        return getattr(value, "name", None) or str(value)

    def _fail(self, message: str) -> None:
        raise ScenarioAssertionError(message, self.transcript)


def fake_attachment(filename: str = "image.png", *,
                    content: bytes = b"fake-image-bytes",
                    size: Optional[int] = None,
                    width: Optional[int] = 800, height: Optional[int] = 400,
                    content_type: Optional[str] = "image/png") -> SimpleNamespace:
    """A file a user uploaded, described the way Discord describes one: its
    bytes (`read()` runs for real), its measures, and its signed CDN link,
    which Discord lets live a day."""

    async def read():
        return content

    return SimpleNamespace(
        read=read, filename=filename,
        size=len(content) if size is None else size,
        width=width, height=height, content_type=content_type,
        url=f"https://cdn.discordapp.com/attachments/1/2/{filename}?ex=1&is=1&hm=signed",
    )


def _routes(custom_id: str) -> bool:
    """Whether discord.py would route a click on `custom_id` to a dynamic item."""
    return any(
        factory.__discord_ui_compiled_template__.fullmatch(custom_id)
        for factory in _dynamic_items()
    )


async def _route(interaction: FakeInteraction, custom_id: str, base) -> None:
    """Deliver a click to every dynamic item whose template matches, built from
    `base` (the component the message carries) as discord.py builds it."""
    for factory in _dynamic_items():
        match = factory.__discord_ui_compiled_template__.fullmatch(custom_id)
        if match is not None:
            dynamic = await factory.from_custom_id(interaction, base, match)
            await dynamic.callback(interaction)


def _dynamic_items() -> List[type]:
    """What discord.py tries on a click no view holds: the dynamic items the bot
    registers at startup."""
    from app.bot import registered_items

    return list(registered_items())
