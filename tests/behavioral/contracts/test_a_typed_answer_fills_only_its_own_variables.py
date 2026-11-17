"""Contract: a typed answer fills only its own variables and shows the rest as typed.

The owner's rule: what a person types in a modal renders only the brace variables
that field supports, the ones its code fills (`{user}`, `{server}`, `{streamer}`...).
Keiko's tokens (`$count`, `$feature`, `$$`, `${context.prefix}`, `${answer.x}`,
`${ref.x}`, `${prefix}ping`), their old spellings (`{context:prefix}`, `{answer:x}`,
`{ref: x}`) and any brace the field does not fill stay exactly as typed.

Every free-text input of every `form.yml` is found from the compiled definitions, and
the lifecycle confirmation is the one modal outside them. For each input a probe
holding every token kind, every old spelling, unknown braces, the variables of the
other fields and the field's own is typed through the real modal, cut to the input's
`max_length` into as many probes as it takes, each in a server of its own whose name
holds braces and a token too. The run then walks every screen the answer reaches: the
card, the modal reopened, the error a refusal tells, the preview, the review, the
panel, its lists, searches and edits, the last-item warning, the history and the
pause, the saved document and the message the feature sends. A screen that shows the
probe shows it character for character; the previews (the lines of a card that
previews its message included) and the message the feature sends show it with the
field's own variables filled, each value once and as it is, and nothing else. A field another input writes into (`fills`) is typed before and after
that input, since the fill writes the value into the typed text.
"""

import random
import re
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import discord
import pytest

from app.commands.birthdays.celebration import celebrate_in_guild
from app.commands.birthdays.service import find_birthdays_on
from app.commands.block_links.service import check_message
from app.commands.twitch.service import announce_live, notices_of
from app.commands.welcome.service import send_welcome_message
from app.commands.youtube.service import VideoAnnouncement, deliver_video
from app.core.commands import form_file, form_files
from app.core.dates import format_mm_dd_label
from app.core.utils import ml, split_messages
from app.forms.form.form_yaml import (
    CardStep,
    ListStep,
    ModalInputSection,
    TextStep,
    TitleContentSection,
    registry,
)
from app.forms.form.responses.validations import VALIDATORS
from tests.behavioral.harness import locators
from tests.generators.birthdays import saved_birthday
from tests.mocks.discord import create_guild, create_member, create_message
from tests.test_admin_templates import filled_names

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

LOCALE = "en-us"
SERVER = "Club {date} {user} $count"
KEIKO_TOKENS = (
    "$count",
    "$feature",
    "$$",
    "${prefix}ping",
    "${context.prefix}",
    "${answer.x}",
    "${plural.x|a|b}",
    "${ref.channel}",
)
OLD_SPELLINGS = ("{context:prefix}", "{answer:x}", "{ref: x}")
STRANGERS = ("{admin}", "{foo}")
FILLED = object()

VARIABLES: Mapping[tuple[str, str], frozenset[str]] = {
    ("block_links", "answer"): frozenset({"user"}),
    ("block_links", "link"): frozenset(),
    ("notifications_twitch", "notification_messages"): frozenset(
        {"streamer", "stream_link"}
    ),
    ("notifications_twitch", "streamer"): frozenset(),
    ("notifications_youtube_video", "notification_messages"): frozenset(
        {"youtuber", "video_link"}
    ),
    ("notifications_youtube_video", "youtuber"): frozenset(),
    ("reminders_birthday", "custom_message_content"): frozenset(
        {"user", "server", "date"}
    ),
    ("reminders_birthday", "custom_message_title"): frozenset(
        {"user", "server", "date"}
    ),
    ("reminders_birthday", "day"): frozenset(),
    ("reminders_birthday", "default_message_content"): frozenset(
        {"user", "server", "date"}
    ),
    ("reminders_birthday", "default_message_title"): frozenset(
        {"user", "server", "date"}
    ),
    ("stream_elements_commands", "streamer"): frozenset(),
    ("welcome_messages", "welcome_messages"): frozenset(
        {"user", "server", "member_count"}
    ),
    ("welcome_messages", "welcome_messages_footer"): frozenset(),
    ("welcome_messages", "welcome_messages_title"): frozenset(),
}


@dataclass(frozen=True)
class Typed:
    """One free-text input of a form: where it is drawn, the key it writes, and the
    section of its card that writes a value into it."""

    form: str
    step: str
    section: int | None
    position: int
    key: str
    validation: str | None
    within: str | None
    filler: int | None = None
    fills: Mapping[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """How the run is named: the form, the key and the input's position."""
        return f"{self.form}.{self.key}#{self.position}"

    @property
    def supported(self) -> frozenset[str]:
        """The variables the field's code fills."""
        return VARIABLES[(self.form, self.key)]


def typed_fields() -> list[Typed]:
    """Every free-text input of every form, found from its compiled definition."""
    return [
        typed
        for form in sorted(form_files())
        for typed in _typed(form, registry.get(form).steps, None)
    ]


def _typed(form: str, steps: Sequence[Any], within: str | None) -> Iterator[Typed]:
    for step in steps:
        if isinstance(step, ListStep):
            yield from _typed(form, step.steps, step.key)
        elif isinstance(step, TextStep):
            checked = step.validation.name if step.validation else None
            yield Typed(form, step.key, None, 0, step.key, checked, within)
        elif isinstance(step, CardStep):
            yield from _card_inputs(form, step, within)


def _card_inputs(form: str, card: CardStep, within: str | None) -> Iterator[Typed]:
    fillers = {
        section.fills.into: (index, dict(section.fills.tokens))
        for index, section in enumerate(card.sections)
        if isinstance(section, ModalInputSection) and section.fills is not None
    }
    for index, section in enumerate(card.sections):
        inputs: list[tuple[str, str | None]] = []
        if isinstance(section, TitleContentSection):
            inputs = [
                (section.keys.title or "", None),
                (section.keys.description or "", None),
            ]
        if isinstance(section, ModalInputSection):
            check = section.modal.validation
            inputs = [
                (typed.key or section.keys.value or "", check.name if check else None)
                for typed in section.modal.inputs
            ]
        for position, (key, checked) in enumerate(inputs):
            filler, fills = fillers.get(key, (None, {}))
            yield Typed(
                form, card.key, index, position, key, checked, within, filler, fills
            )


@dataclass(frozen=True)
class Probe:
    """What is typed, the word that marks it on screen, and whether the field's
    check takes it."""

    text: str
    marker: str
    accepted: bool


def _pieces(typed: Typed, spaced: bool) -> list[str]:
    everyone = sorted({name for names in VARIABLES.values() for name in names})
    others = [f"{{{name}}}" for name in everyone if name not in typed.supported]
    own = [f"{{{name}}}" for name in sorted(typed.supported)]
    old = [spelling if spaced else spelling.replace(" ", "") for spelling in OLD_SPELLINGS]
    return [*KEIKO_TOKENS, *old, *STRANGERS, *others, *own]


def _plain(marker: str, pieces: Sequence[str]) -> str:
    return " ".join((marker, *pieces, marker))


def _linked(marker: str, pieces: Sequence[str]) -> str:
    return "probe.com/" + "/".join((marker, *pieces, marker))


Shape = tuple[Callable[[str, Sequence[str]], str], bool, bool]
SHAPES: Mapping[str | None, Shape] = {
    None: (_plain, True, True),
    "link_or_domain": (_linked, False, True),
    "registered_and_found": (_plain, True, False),
    "date": (_plain, True, False),
}


def probes(typed: Typed, limit: int) -> list[Probe]:
    """The probes of `typed`, each within `limit` characters, together holding every
    piece; an input too short for a marked probe gets the one token that fits."""
    shape, spaced, accepted = SHAPES[typed.validation]
    found: list[Probe] = []
    pending: list[str] = []

    def marked(pieces: Sequence[str]) -> str:
        return shape(f"zz{len(found) + 1}", pieces)

    for piece in _pieces(typed, spaced):
        if pending and len(marked([*pending, piece])) > limit:
            found.append(Probe(marked(pending), f"zz{len(found) + 1}", accepted))
            pending = []
        if len(marked([piece])) <= limit:
            pending.append(piece)
    if pending:
        found.append(Probe(marked(pending), f"zz{len(found) + 1}", accepted))
    return found or [Probe("$$", "$$", accepted)]


def shown_as(text: str, values: Mapping[str, Any]) -> re.Pattern[str]:
    """What `text` reads once each of `values` is filled, in one pass: a value given
    as `FILLED` may be anything but its own brace."""
    if not values:
        return re.compile(re.escape(text))
    names = "|".join(re.escape(name) for name in values)
    parts = re.split(r"\{(" + names + r")\}", text)
    written = []
    for index, part in enumerate(parts):
        if index % 2 == 0:
            written.append(re.escape(part))
        elif values[part] is FILLED:
            written.append(r"(?!\{" + re.escape(part) + r"\}).*?")
        else:
            written.append(re.escape(str(values[part])))
    return re.compile("".join(written), re.DOTALL)


def strings(node: Any) -> Iterator[str]:
    """Every text an event or a document holds."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, Mapping):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from strings(value)


def shown_texts(events: Sequence[Mapping[str, Any]]) -> list[str]:
    """Every text the bot showed in `events`."""
    return [
        text for event in events if event["actor"] == "bot" for text in strings(event)
    ]


def written(sent: Any) -> list[str]:
    """The texts of a message's content or of an embed."""
    if sent is None:
        return []
    if isinstance(sent, str):
        return [sent]
    footer = sent.footer.text if sent.footer else ""
    fields = [str(item.value) for item in sent.fields]
    return [sent.title or "", sent.description or "", footer or "", *fields]


def channel_texts(channel: Any) -> list[str]:
    """What `channel` received, content and embeds."""
    return [
        text
        for message in channel.get_all_messages()
        for sent in (message.content, *message.embeds)
        for text in written(sent)
    ]


def each_message(count: int) -> Iterator[int]:
    """`random.choice` patched to pick each of `count` messages in turn."""
    for index in range(count):
        with patch.object(random, "choice", lambda options, at=index: options[at]):
            yield index


@dataclass
class Run:
    """One probe typed in one server, and the screens that showed it."""

    scenario: Any
    deps: Any
    guild: Any
    typed: Typed
    typed_first: bool = False
    probe: Probe | None = None
    baked: dict[str, str] = field(default_factory=dict)
    cursor: int = 0
    seen: dict[str, int] = field(default_factory=dict)

    def check(
        self,
        surface: str,
        values: Mapping[str, Any] | None = None,
        texts: Sequence[str] | None = None,
        shown: bool = True,
    ) -> None:
        """Every text since the last check that holds the probe's marker holds the
        probe: as typed, or with the field's own `values` filled in."""
        assert self.probe is not None
        if texts is None:
            texts = shown_texts(self.scenario.outputs[self.cursor :])
            self.cursor = len(self.scenario.outputs)
        expected = shown_as(self.probe.text, {**(values or {}), **self.baked})
        marked = [text for text in texts if self.probe.marker in text]
        wrong = [text for text in marked if not expected.search(text)]
        assert not wrong, (
            f"{self.typed.name} on {surface}: typed {self.probe.text!r}, shown "
            f"{wrong[0]!r}\n{self.scenario.transcript}"
        )
        assert marked or not shown, (
            f"{self.typed.name}: {surface} never showed {self.probe.text!r}\n"
            f"{self.scenario.transcript}"
        )
        self.seen[surface] = self.seen.get(surface, 0) + len(marked)

    def modal_limit(self) -> int:
        """The most characters the pending modal's input of `typed` accepts."""
        modal = self.scenario.store.pending_modal
        inputs = self.scenario._modal_inputs(modal)
        return int(inputs[self.typed.position].max_length or 4000)

    async def submit(self) -> None:
        """The probe typed in its input of the pending modal, the others kept."""
        assert self.probe is not None
        label = self.scenario.pending_modal_fields()[self.typed.position]
        await self.scenario.submit_modal({label: self.probe.text})

    def saved(self) -> Mapping[str, Any]:
        """The document the form saved for this server."""
        found = self.scenario.get_persisted(
            "guild", self.typed.form, {"guild_id": str(self.guild.id)}
        )
        assert found is not None, self.scenario.transcript
        return found


def member_of(guild: Any, member_id: int, name: str) -> Any:
    """A member of `guild` the welcome code can mention."""
    member = create_member(guild, id=member_id, name=name)
    member._user = SimpleNamespace(id=member_id)
    return member


async def previews(run: Run, values: Mapping[str, Any]) -> None:
    """Every Preview on the screen clicked, each page read, the preview dismissed."""
    scenario = run.scenario
    message = scenario.current_message
    label = ml("buttons.preview.label", locale=LOCALE)
    targets = [
        locators.codec_target(item)
        for item in locators.clickable_items(message)
        if isinstance(item, discord.ui.Button) and item.label == label
    ]
    for target in targets:
        run.check("screen", shown=False)
        await scenario.click(target or "preview")
        await scenario.finish()
        before = run.seen.get("preview", 0)
        run.check("preview", values, shown=False)
        shown = scenario.current_message
        if shown is not message:
            pages = [*shown.view.pages, shown.view.embed]
            texts = [text for page in pages for text in written(page)]
            run.check("preview", values, texts, shown=False)
            shown.deleted = True
        assert run.seen["preview"] > before, (
            f"{run.typed.name}: the preview of {target} never showed the probe\n"
            f"{scenario.transcript}"
        )


async def open_section(run: Run, section: int | None = None) -> None:
    """The modal of the card section that holds `typed`, or of `section`."""
    chosen = run.typed.section if section is None else section
    await run.scenario.click(f"customize:{chosen}")


async def reopened(run: Run, surface: str = "modal reopened") -> None:
    """The field's modal opened again shows the probe, then is closed."""
    await open_section(run)
    run.check(surface)
    run.scenario.dismiss_modal()


async def lifecycle(run: Run) -> None:
    """The history, then Pause, from the panel: neither reads the probe."""
    scenario = run.scenario
    panel = scenario.current_message
    await scenario.click("history")
    run.check("history", shown=False)
    if scenario.current_message is not panel:
        scenario.current_message.deleted = True
    await scenario.click("pause")
    await scenario.submit_confirmation()
    run.check("paused", shown=False)


def saved(run: Run) -> None:
    """The saved document holds the probe as typed, wherever it holds it."""
    run.check("saved document", texts=list(strings(dict(run.saved()))))


class Journey:
    """How a form is walked: the screen an input is typed on, what follows, and the
    message the feature sends from what was saved."""

    form = ""
    previewed = False

    async def card(self, run: Run) -> None:
        """The card that holds `run.typed`, every other part it needs answered."""
        raise NotImplementedError

    async def open(self, run: Run) -> None:
        """The modal `run.typed` is typed in."""
        await open_section(run)

    def preview_values(self, run: Run) -> Mapping[str, Any]:
        """What the preview fills each variable of the field with."""
        return {}

    async def refused(self, run: Run) -> None:
        """A probe the field's check refuses: the error, the modal reopened."""
        run.check("refusal", shown=False)
        await reopened(run)

    async def typed(self, run: Run) -> None:
        """The card the probe was typed on, and the input that fills it afterwards."""
        run.check("card")

    async def accepted(self, run: Run) -> None:
        """A probe the field takes: every screen up to the message the feature sends."""
        scenario = run.scenario
        await self.typed(run)
        await reopened(run)
        if self.previewed:
            await previews(run, self.preview_values(run))
        await scenario.click("done")
        run.check("review", shown=False)
        await previews(run, self.preview_values(run))
        await scenario.confirm()
        await scenario.finish()
        await self.panel(run)
        await previews(run, self.preview_values(run))
        saved(run)
        await self.sent(run)
        await self.edit(run)
        await lifecycle(run)

    async def panel(self, run: Run) -> None:
        """The panel after the save, and the screens it opens on the probe."""
        run.check("panel", shown=False)

    async def edit(self, run: Run) -> None:
        """The field edited again from the panel: its card and its modal, then back."""

    async def sent(self, run: Run) -> None:
        """The message the feature sends from what was saved."""


async def edited(run: Run, *opening: str) -> None:
    """The card `opening` reaches from the panel shows the probe, and so does the
    modal of its section, then Cancel and Discard, back to the panel."""
    scenario = run.scenario
    for target in opening:
        await scenario.click(target)
    run.check("panel edit")
    await reopened(run, "modal from the panel")
    card = scenario.current_message
    await scenario.cancel()
    await scenario.click(ml("buttons.discard.label", locale=LOCALE))
    await scenario.finish()
    if scenario.current_message is not card:
        scenario.current_message.deleted = True
    run.check("panel", shown=False)


class Welcome(Journey):
    form = "welcome_messages"
    previewed = True

    async def card(self, run: Run) -> None:
        await run.scenario.confirm()
        await run.scenario.select_option("welcome", target="welcome_messages_channel")

    def preview_values(self, run: Run) -> Mapping[str, Any]:
        run.scenario.user._user = SimpleNamespace(id=run.scenario.user.id)
        return self._values(run, f"<@!{run.scenario.user.id}>")

    @staticmethod
    def _values(run: Run, mention: str) -> Mapping[str, Any]:
        everything = {
            "user": mention,
            "server": run.guild.name,
            "member_count": str(run.guild.member_count),
        }
        return {name: everything[name] for name in run.typed.supported}

    async def sent(self, run: Run) -> None:
        member = member_of(run.guild, 4242, "Newcomer")
        messages = run.saved()["welcome_messages"]["values"]
        for _index in each_message(len(split_messages(messages))):
            await send_welcome_message(member)
        channel = run.guild.get_channel(101)
        run.check("welcome sent", self._values(run, "<@!4242>"), channel_texts(channel))

    async def edit(self, run: Run) -> None:
        await run.scenario.click("section:welcome_config/messages")
        run.check("modal from the panel")
        run.scenario.dismiss_modal()


class Subscription(Journey):
    previewed = True
    account = ""
    account_key = ""
    link_name = ""

    def known(self, run: Run) -> None:
        """The account the card's lookup finds."""
        raise NotImplementedError

    def link(self) -> str:
        """The link the preview fills."""
        raise NotImplementedError

    async def card(self, run: Run) -> None:
        self.known(run)
        scenario = run.scenario
        await scenario.confirm()
        await scenario.select_option("general", target="channel")
        if run.typed.key != self.account_key and not run.typed_first:
            await self._account(run)

    async def _account(self, run: Run) -> None:
        scenario = run.scenario
        await open_section(run, run.typed.filler)
        await scenario.submit_modal({scenario.pending_modal_fields()[0]: self.account})
        await scenario.finish()

    async def typed(self, run: Run) -> None:
        run.check("card")
        if not run.typed_first:
            return
        await self._account(run)
        events = run.scenario.outputs[run.cursor :]
        run.cursor = len(run.scenario.outputs)
        run.check("card looking up", texts=shown_texts(events[:-1]), shown=False)
        run.baked = {
            name: template.replace("{value}", self.account)
            for name, template in run.typed.fills.items()
        }
        run.check("card after the fill", texts=shown_texts(events[-1:]))

    def preview_values(self, run: Run) -> Mapping[str, Any]:
        everything = {self.account_key: self.account, self.link_name: self.link()}
        return {name: everything[name] for name in run.typed.supported}

    async def panel(self, run: Run) -> None:
        scenario = run.scenario
        run.check("panel")
        await scenario.click("remove_this:notifications$0")
        run.check("last item warning")
        await scenario.go_back()

    async def edit(self, run: Run) -> None:
        await edited(run, "section:notifications$0")


class Twitch(Subscription):
    form = "notifications_twitch"
    account = "gaules"
    account_key = "streamer"
    link_name = "stream_link"

    def known(self, run: Run) -> None:
        run.deps.twitch.add_user("gaules", user_id="181077473")

    def link(self) -> str:
        return "https://www.twitch.tv/gaules"

    async def sent(self, run: Run) -> None:
        run.deps.bot.get_guild = lambda _guild_id: run.guild
        notices = notices_of([dict(run.saved())], self.account)
        guild_data, notification = notices[0]
        messages = split_messages(notification["notification_messages"]["value"])
        user = {"login": self.account, "id": "181077473", "profile_image_url": ""}
        for _index in each_message(len(messages)):
            await announce_live(guild_data, notification, {"title": "Live"}, user)
        channel = run.guild.get_channel(100)
        run.check("live sent", self.preview_values(run), channel_texts(channel))


class YouTube(Subscription):
    form = "notifications_youtube_video"
    account = "pewdiepie"
    account_key = "youtuber"
    link_name = "video_link"

    def known(self, run: Run) -> None:
        run.deps.youtube.add_channel(
            "UC-lHJZR3Gqxm24_Vd_AJ5Yw", "PewDiePie", custom_url="@pewdiepie"
        )

    def link(self) -> str:
        return "https://www.youtube.com/@pewdiepie"

    async def sent(self, run: Run) -> None:
        run.deps.bot.get_guild = lambda _guild_id: run.guild
        document = dict(run.saved())
        notification = document["notifications"]["values"][0]
        messages = split_messages(notification["notification_messages"]["value"])
        announcement = VideoAnnouncement(
            video={"id": "video-1", "snippet": {"title": "A new video"}},
            channel={"title": "PewDiePie"},
            youtuber=self.account,
            followers=[document],
        )
        for _index in each_message(len(messages)):
            await deliver_video(announcement, document, notification)
        everything = {
            "youtuber": self.account,
            "video_link": "https://www.youtube.com/watch?v=video-1",
        }
        values = {name: everything[name] for name in run.typed.supported}
        channel = run.guild.get_channel(100)
        run.check("video sent", values, channel_texts(channel))


class Birthdays(Journey):
    form = "reminders_birthday"
    friend = 777

    async def card(self, run: Run) -> None:
        scenario = run.scenario
        create_member(run.guild, id=self.friend, name="Amiga")
        await scenario.confirm()
        await scenario.select_option("general", target="channel")
        await scenario.select_option("America/Sao_Paulo", target="timezone")
        await scenario.select_option("08:00", target="notification_time")
        if run.typed.within is None:
            return
        await scenario.click("done")
        await scenario.confirm()
        await scenario.finish()
        await scenario.click("add")
        await scenario.select_option("Amiga", target="user")
        await scenario.select_option("05", target="month")
        if run.typed.key != "day":
            await scenario.click("customize:2")
            await scenario.submit_modal({scenario.pending_modal_fields()[0]: "12"})

    def _values(
        self, run: Run, mention: str, mm_dd: str | object
    ) -> Mapping[str, Any]:
        named = "Amiga" if mention == f"<@{self.friend}>" else run.scenario.user.name
        day = mm_dd if mm_dd is FILLED else format_mm_dd_label(str(mm_dd), LOCALE)
        everything = {
            "user": named if run.typed.position == 0 else mention,
            "server": run.guild.name,
            "date": day,
        }
        return {key: everything[key] for key in run.typed.supported}

    def preview_values(self, run: Run) -> Mapping[str, Any]:
        if run.typed.within is None:
            today = date.today().strftime("%m-%d")
            return self._values(run, f"<@{run.scenario.user.id}>", today)
        return self._values(run, f"<@{self.friend}>", FILLED)

    async def accepted(self, run: Run) -> None:
        if run.typed.within is None:
            await super().accepted(run)
            return
        scenario = run.scenario
        on_card = {**self._values(run, f"<@{self.friend}>", FILLED)}
        on_card.update({"user": f"<@{self.friend}>"} if "user" in on_card else {})
        run.check("card", on_card)
        await reopened(run)
        await previews(run, self.preview_values(run))
        await scenario.click("done")
        await scenario.finish()
        run.check("panel", shown=False)
        await scenario.click("search")
        await scenario.select_option("Amiga")
        run.check("search")
        await self.sent(run)
        await scenario.click("edit")
        run.check("panel edit", on_card)
        await reopened(run, "modal from the panel")

    async def edit(self, run: Run) -> None:
        await edited(run, "edit:group:settings", "section:birthday_config")
        await run.scenario.go_back()

    async def sent(self, run: Run) -> None:
        run.deps.bot.get_guild = lambda _guild_id: run.guild
        guild_id, user_id = str(run.guild.id), str(self.friend)
        if run.typed.within is None:
            saved_birthday(guild_id, user_id, "05-12")
        items = await find_birthdays_on(guild_id, "05-12")
        await celebrate_in_guild(guild_id, items, "05-12", Counter())
        values = self._values(run, f"<@{self.friend}>", "05-12")
        channel = run.guild.get_channel(100)
        run.check("birthday sent", values, channel_texts(channel))


class BlockLinks(Journey):
    form = "block_links"

    async def card(self, run: Run) -> None:
        await run.scenario.confirm()

    async def open(self, run: Run) -> None:
        if run.typed.key != "link":
            await open_section(run)
            return
        await run.scenario.click("customize:2")
        await run.scenario.click("customize:6")

    async def accepted(self, run: Run) -> None:
        if run.typed.key != "link":
            await super().accepted(run)
            return
        scenario = run.scenario
        run.check("how far the rule goes")
        await scenario.click("option:🌐 Every link from this website")
        run.check("exceptions screen")
        await scenario.go_back()
        run.check("card", shown=False)
        await scenario.click("done")
        run.check("review")
        await scenario.confirm()
        await scenario.finish()
        run.check("panel")
        saved(run)
        await scenario.click("remove:custom_links")
        run.check("removal picker")
        await scenario.select_option("custom_links$0")
        await scenario.click("remove_items")
        run.check("removal confirmation", shown=False)

    async def edit(self, run: Run) -> None:
        await edited(run, "section:link_settings")

    async def sent(self, run: Run) -> None:
        author = create_member(run.guild, id=4343, name="Linker")
        channel = run.guild.get_channel(100)
        await check_message(
            str(run.guild.id), create_message(channel, author, "https://spam.com/x")
        )
        run.check("block reply", {"user": author.mention}, channel_texts(channel))


class StreamElements(Journey):
    form = "stream_elements_commands"

    async def card(self, run: Run) -> None:
        return None

    async def open(self, run: Run) -> None:
        await run.scenario.confirm()

    async def refused(self, run: Run) -> None:
        run.check("refusal")
        await run.scenario.click("customize:0")
        run.check("modal reopened")
        run.scenario.dismiss_modal()


JOURNEYS: Mapping[str, Journey] = {
    journey.form: journey
    for journey in (
        Welcome(),
        Twitch(),
        YouTube(),
        Birthdays(),
        BlockLinks(),
        StreamElements(),
    )
}
TYPED = typed_fields()
CASES = [
    *(pytest.param(typed, False, id=typed.name) for typed in TYPED),
    *(
        pytest.param(typed, True, id=f"{typed.name} typed before its fill")
        for typed in TYPED
        if typed.fills
    ),
]


@pytest.fixture(autouse=True)
def _cooled_down(monkeypatch):
    """The admin waits out the cooldown between two previews of one run."""
    from app.forms.discord import callbacks

    monkeypatch.setattr(callbacks, "_cooling", lambda *_args: False)


def test_every_typed_field_of_every_form_is_probed():
    """A new modal input fails here until its variables and its walk are known."""
    found = {(typed.form, typed.key) for typed in TYPED}

    assert found == set(VARIABLES)
    assert {typed.form for typed in TYPED} <= set(JOURNEYS)
    assert {typed.validation for typed in TYPED} <= set(SHAPES)
    assert set(SHAPES) - {None} <= set(VALIDATORS)


def test_each_field_names_only_variables_its_command_fills():
    """The variables a field supports are the ones the `fill_placeholders` calls of
    its command's folder pass, and every one of them is some field's."""
    for form in {typed.form for typed in TYPED}:
        path = form_file(form)
        assert path is not None
        own = set().union(*(names for (key, _), names in VARIABLES.items() if key == form))
        assert own == filled_names(path.parent), form


@pytest.mark.regression
@pytest.mark.parametrize("typed,typed_first", CASES)
async def test_a_typed_answer_shows_as_typed_and_fills_only_its_variables(
    scenario_factory, deps, typed, typed_first
):
    """What broke: the lines of a card that previews its message (the birthday
    member card) filled `{user}`, `{server}` and `{date}` one after the other, so the
    server's name was read again: in a server named `Club {date} {user} $count` a
    typed `{server}` showed `Club  {user} $count`. The shared screen is the
    `title_content` section's lines in `configuration_card.py`, now filled by
    `templated` in one pass like `fill_placeholders`. What must hold: every value
    put into a typed text is shown as it is, and nothing else in it is read."""
    journey = JOURNEYS[typed.form]
    found: list[Probe] | None = None
    index = 0

    while found is None or index < len(found):
        guild = create_guild(id=70_000 + index, name=SERVER)
        scenario = await scenario_factory(locale=LOCALE, guild=guild).start_command(
            typed.form
        )
        run = Run(scenario, deps, guild, typed, typed_first)
        await journey.card(run)
        await journey.open(run)
        found = found or probes(typed, run.modal_limit())
        run.probe = found[index]
        run.check("before the probe", shown=False)
        await run.submit()
        await scenario.finish()
        if run.probe.accepted:
            await journey.accepted(run)
        else:
            await journey.refused(run)
        print(f"{typed.name} probe {index + 1}/{len(found)}: {run.seen}")
        index += 1


async def test_the_typed_confirmation_of_a_lifecycle_is_never_shown(
    scenario_factory, deps
):
    """The word a pause asks for is only compared: a probe typed there pauses
    nothing and no screen shows it."""
    guild = create_guild(id=79_000, name=SERVER)
    scenario = await scenario_factory(locale=LOCALE, guild=guild).start_command(
        "block_links"
    )
    typed = Typed("block_links", "confirmation", None, 0, "word", None, None)
    probe = Probe(_plain("zz1", KEIKO_TOKENS), "zz1", False)
    run = Run(scenario, deps, guild, typed, probe=probe)
    await scenario.confirm()
    await scenario.click("done")
    await scenario.confirm()
    await scenario.finish()
    await scenario.click("pause")
    await scenario.submit_confirmation(word=probe.text)

    run.check("after the confirmation", shown=False)
    assert run.saved()["enabled"] is True
