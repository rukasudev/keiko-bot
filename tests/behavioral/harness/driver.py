"""FormScenario: drives real Keiko form flows offline, on the form platform.

Entry points are the production seams every cog funnels through: the
adapter's `open_feature` (a slash command, a `/setup` or a greeting button).
Everything downstream — YAML definitions, the engine, the renderer, i18n,
features, the data layer — is real; only the Discord transport
(FakeInteraction) and Mongo/Redis (existing mocks) are fake.
"""
import asyncio
import copy
import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Union

import discord

from app.forms.adapters.discord.entrypoints import RUNTIME
from app.forms.adapters.discord.ids import decode as decode_component_id
from app.forms.kinds.manage import confirmation_word
from tests.behavioral.harness import locators
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


class FormScenario:
    def __init__(self, *, guild, user, locale: str, mongo=None):
        self.locale_str = locale
        self.locale = _LOCALES[locale]
        self.guild = guild
        self.user = user
        self.db = mongo
        self.store = MessageStore()
        self.command_key: Optional[str] = None

    # ------------------------------------------------------------------ setup

    async def start(self, command_key: str) -> "FormScenario":
        """Open the command with nothing saved: the setup form."""
        self.command_key = command_key
        self.store.record("start", actor="user", target=command_key,
                          values=self.locale_str)
        await self._open_feature(self._mint(), command_key)
        return self

    async def start_manager(self, command_key: str, cog_data: Dict[str, Any]) -> "FormScenario":
        """Open the command over a saved document: the manager panel."""
        self.command_key = command_key
        self.store.record("start_manager", actor="user", target=command_key,
                          values=self.locale_str)
        self._seed_document(command_key, cog_data)
        await self._open_feature(self._mint(), command_key)
        return self

    async def start_command(self, command_key: str) -> "FormScenario":
        """Open the command the way a slash command or a /setup button does:
        the platform routes to the setup form or the manager from what is
        saved (seed Mongo first)."""
        self.command_key = command_key
        self.store.record("start_command", actor="user", target=command_key,
                          values=self.locale_str)
        await self._open_feature(self._mint(), command_key)
        return self

    async def _open_feature(self, interaction, command_key: str) -> None:
        await RUNTIME.open_feature(interaction, command_key)
        self.store.step_provider = self._current_step_key

    def _seed_document(self, command_key: str, cog_data: Dict[str, Any]) -> None:
        """What the old manager received as an argument, the platform reads from Mongo."""
        document = copy.deepcopy(cog_data)
        document["guild_id"] = str(self.guild.id)
        collection = self.db.guild[command_key]
        if collection.find_one({"guild_id": str(self.guild.id)}) is None:
            collection.insert_one(document)
        self.db.guild.moderations.insert_one(
            {"guild_id": str(self.guild.id), command_key: True}
        )

    # ------------------------------------------------------------ user actions

    async def click(self, target: str) -> None:
        message = self._require_message()
        button = locators.find_button(message, target, self.locale)
        custom_id = button.custom_id if getattr(button, "_provided_custom_id", False) else None
        self.store.record("click", actor="user", message=message.id, target=target,
                          custom_id=None if is_session_coded(custom_id) else custom_id)
        interaction = self._mint(message=message, custom_id=custom_id)
        await locators.dispatch_click(message.view, button, interaction)

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
        interaction = self._mint(message=message)
        await select.callback(interaction)

    async def submit_modal(self, fields: Dict[str, str]) -> None:
        modal = self.store.pending_modal
        if modal is None:
            raise LocatorError("no modal is pending", self.transcript)
        inputs = self._modal_inputs(modal)
        for key, value in fields.items():
            matched = self._match_input(inputs, key)
            matched._value = value
        self.store.pending_modal = None
        self.store.record("modal_submit", actor="user",
                          fields={k: v for k, v in fields.items()})
        interaction = self._mint(message=self.current_message)
        await modal.on_submit(interaction)

    def dismiss_modal(self) -> None:
        """The user closes the modal without submitting (Esc / clicking away).
        Discord tells the bot NOTHING when this happens: no interaction, no
        event. The message that opened the modal stays on screen unchanged."""
        if self.store.pending_modal is None:
            raise LocatorError("no modal is pending", self.transcript)
        self.store.pending_modal = None
        self.store.record("modal_dismiss", actor="user")

    def pending_modal_fields(self) -> List[str]:
        modal = self.store.pending_modal
        if modal is None:
            raise LocatorError("no modal is pending", self.transcript)
        return [i.label for i in self._modal_inputs(modal)]

    async def submit_confirmation(self, word: Optional[str] = None) -> None:
        """Submit the typed confirmation of a lifecycle action (pause, unpause,
        disable). Defaults to the correct word; pass a wrong `word` to test
        the rejection path."""
        modal = self.store.pending_modal
        if modal is None:
            raise LocatorError("no confirmation modal is pending", self.transcript)
        typed = word if word is not None else self._confirmation_word(modal)
        inputs = self._modal_inputs(modal)
        inputs[0]._value = typed
        self.store.pending_modal = None
        self.store.record("modal_submit", actor="user", fields={"confirmation": typed})
        await modal.on_submit(self._mint(message=self.current_message))

    async def submit_file_upload(self, *, filename: str = "image.png",
                                 content: bytes = b"fake-image-bytes") -> None:
        """Submit a pending file-upload modal with a fake attachment."""
        modal = self.store.pending_modal
        if modal is None or getattr(modal, "file_upload", None) is None:
            raise LocatorError("no file-upload modal is pending", self.transcript)

        async def read():
            return content

        attachment = SimpleNamespace(read=read, filename=filename)
        modal.file_upload._values = [attachment]
        self.store.pending_modal = None
        self.store.record("modal_submit", actor="user", fields={"file": filename})
        await modal.on_submit(self._mint(message=self.current_message))

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
        if kind is not None and event["kind"] != kind:
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

    def expect_error(self, message_contains: str) -> None:
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
            labels = [getattr(i, "label", None) for i in self._modal_inputs(modal)]
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
    def card_state(self) -> Dict[str, Any]:
        """The state the card on screen is drawn from (defaults included)."""
        from app.forms.definitions.registry import registry
        from app.forms.definitions.schema import CardStep
        from app.forms.engine.decide import steps_for
        from app.forms.engine.documents import document_values
        from app.forms.engine.rules import Scope
        from app.forms.kinds.card import state_of
        from app.forms.kinds.context import RenderContext

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
        context = RenderContext(
            definition=definition,
            steps=steps,
            locale=self.locale_str,
            scope=Scope(session.values()),
            document=document_values(document),
        )
        return state_of(step, session, context)

    @property
    def answers(self) -> Dict[str, Any]:
        """The raw answers of the session on screen, by key."""
        session = self.session
        return dict(session.values()) if session is not None else {}

    # ---------------------------------------------------------------- internal

    def _mint(self, message: Optional[FakeMessage] = None,
              custom_id: Optional[str] = None) -> FakeInteraction:
        data = {"custom_id": custom_id} if custom_id else {}
        return FakeInteraction(self.store, guild=self.guild, user=self.user,
                               locale=self.locale, message=message, data=data)

    def _require_message(self) -> FakeMessage:
        message = self.current_message
        if message is None or message.view is None:
            raise LocatorError("no live message with components", self.transcript)
        return message

    def _current_step_key(self) -> Optional[str]:
        session = self.session
        return session.cursor if session is not None else None

    def _confirmation_word(self, modal) -> Optional[str]:
        component = decode_component_id(getattr(modal, "custom_id", None) or "")
        if component is not None and component.action == "word":
            return confirmation_word(component.arg or "", self.locale_str)
        return None

    def _last_content_event(self) -> Dict[str, Any]:
        for event in reversed(self.store.events):
            if event["actor"] == "bot" and event["kind"] in _CONTENT_KINDS:
                return event
        self._fail("no bot message event recorded yet")

    def _modal_inputs(self, modal) -> List[Any]:
        inputs = []
        for child in modal.children:
            inner = getattr(child, "component", child)
            if isinstance(inner, discord.ui.TextInput):
                inputs.append(inner)
        return inputs

    def _match_input(self, inputs: List[Any], key: str):
        for text_input in inputs:
            if (text_input.label or "").lower() == key.lower():
                return text_input
        for text_input in inputs:
            if key.lower() in (text_input.label or "").lower():
                return text_input
        raise LocatorError(
            f"modal has no field matching {key!r} "
            f"(fields: {[i.label for i in inputs]})",
            self.transcript,
        )

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
