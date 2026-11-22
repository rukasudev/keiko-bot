"""The command registry, its dispatch and the cogs it loads, against a fake
`app.commands.COMMANDS`.

The registry reads the list lazily and once, so each test installs its own list as
the `app.commands` module and forgets the cached read before and after (`declare`,
in `tests/core/conftest.py`).
"""

import logging
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import dispatch, groups, trace
from app.core.commands import (
    PARENT_GROUPS,
    Command,
    GuildJoined,
    Hooks,
    JoinStage,
    MemberJoined,
    MessageEdited,
    MessageSent,
    Reminder,
    WebhookEvent,
    WebhookReply,
    command_for,
    commands,
    member_hooks,
    message_hooks,
    prefix_hooks,
    reminder_handlers,
    schedule_handlers,
    setup_feature,
    setup_features,
    webhook_handlers,
)
from app.core.utils import command_extensions
from app.types.cogs import Cog, Group

pytestmark = pytest.mark.unit


class Stray(Group, name="stray", description="A subgroup for the tests."):
    """A slash subgroup no real command declares."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot
        super().__init__()


class Loose(Cog):
    """A cog whose module has no `setup(bot)`."""


def _recorder(calls: List[str], name: str, answer: Any = None):
    async def hook(_context: Any) -> Any:
        calls.append(name)
        return answer

    return hook


def _failing(calls: List[str], name: str):
    async def hook(_context: Any) -> None:
        calls.append(name)
        raise RuntimeError(f"{name} broke")

    return hook


def _message(content: str, guild_id: Any = "10") -> MessageSent:
    return MessageSent(guild_id, "20", "30", content, message=None)


def test_commands_keep_the_declared_order_and_read_the_list_once(declare):
    declare(Command("first"), Command("second"), Command("third"))

    assert [command.key for command in commands()] == ["first", "second", "third"]
    assert commands() is commands(), "the list is read once and cached"
    assert command_for("second").key == "second"


def test_an_unknown_key_is_refused(declare):
    declare(Command("first"))

    with pytest.raises(KeyError):
        command_for("missing")


def test_a_key_declared_twice_is_refused(declare):
    declare(Command("same"), Command("same"))

    with pytest.raises(ValueError, match="command key 'same'"):
        commands()


def test_no_command_is_keyed_global(declare):
    """`global` owns the facts and the context every command shares, so a command
    under that key would read as the catalog."""
    declare(Command("global"))

    with pytest.raises(ValueError, match="no command is keyed 'global'"):
        commands()


@pytest.mark.parametrize("key", ["buttons", "commands", "errors", "locales", "texts"])
def test_no_command_is_keyed_like_a_shared_copy_file(declare, key):
    """Each file of `app/languages/` loads its copy under its stem, so a command under
    that key would load its own copy beside the shared one and read either."""
    declare(Command(key))

    with pytest.raises(
        ValueError,
        match=f"no command is keyed '{key}': app/languages/{key}.yml loads the shared",
    ):
        commands()


def test_a_reminder_title_owned_by_two_commands_is_refused(declare):
    declare(
        Command("first", reminders={"renewal": lambda _reminder: None}),
        Command("second", reminders={"renewal": lambda _reminder: None}),
    )

    with pytest.raises(ValueError, match="reminder title 'renewal'"):
        commands()
    with pytest.raises(ValueError, match="reminder title 'renewal'"):
        reminder_handlers()


def test_a_webhook_event_owned_by_two_commands_is_refused(declare):
    def reply(_event: WebhookEvent) -> WebhookReply:
        return WebhookReply(200, "ok")

    declare(
        Command("first", webhooks={"service.event": reply}),
        Command("second", webhooks={"service.event": reply}),
    )

    with pytest.raises(ValueError, match="webhook event 'service.event'"):
        webhook_handlers()


def test_a_schedule_kind_owned_by_two_commands_is_refused(declare):
    async def due(_schedules: Any) -> None:
        return None

    declare(
        Command("first", schedules={"live": due}),
        Command("second", schedules={"live": due}),
    )

    with pytest.raises(ValueError, match="schedule kind 'live'"):
        commands()
    with pytest.raises(ValueError, match="schedule kind 'live'"):
        schedule_handlers()


@pytest.mark.parametrize("placement", [{"group": "moderation"}, {"category": "alerts"}])
def test_a_group_or_category_that_is_no_parent_group_is_refused(declare, placement):
    declare(Command("typo", **placement))

    with pytest.raises(ValueError, match="'typo' declares the .* no parent group"):
        commands()


def test_a_setup_row_in_no_category_is_refused(declare):
    declare(Command("homeless", on_setup=True))

    with pytest.raises(
        ValueError, match="'homeless' is offered on /setup in no category"
    ):
        commands()


def test_the_parent_groups_are_the_table_in_its_order():
    parents = (groups.Moderations, groups.Notifications, groups.Integrations)

    assert [parent.group_key for parent in parents] == list(PARENT_GROUPS)


async def test_a_subgroup_under_no_parent_group_is_refused(declare):
    declare(Command("orphan", cogs=(Stray,)))
    bot = MagicMock()
    bot.add_cog = AsyncMock()

    with pytest.raises(ValueError, match="'orphan' declares the subgroup Stray"):
        await groups.setup(bot)
    assert bot.add_cog.await_count == 0


async def test_a_subgroup_is_attached_under_its_parent_group(declare):
    declare(Command("placed", cogs=(Stray,), group="integrations"))
    bot = MagicMock()
    bot.add_cog = AsyncMock()

    await groups.setup(bot)

    added = {
        cog.group_key: cog for cog in (c.args[0] for c in bot.add_cog.await_args_list)
    }
    assert list(added) == list(PARENT_GROUPS)
    assert [command.name for command in added["integrations"].app_command.commands] == [
        "stray"
    ]


def test_a_cog_whose_module_has_no_setup_is_refused():
    with pytest.raises(ValueError, match="'loose' declares the cog Loose"):
        command_extensions(Command("loose", cogs=(Loose,)))


def test_setup_lists_only_the_commands_with_a_row_in_the_declared_order(declare):
    declare(
        Command("plain"),
        Command("second", group="moderations", on_setup=True),
        Command("first", group="moderations", on_setup=True),
    )

    assert [command.key for command in setup_features()] == ["second", "first"]


def test_the_setup_feature_of_a_key_is_the_command_setup_offers(declare):
    declare(
        Command("plain"),
        Command("offered", group="moderations", on_setup=True),
    )

    assert setup_feature("offered") is command_for("offered")
    assert setup_feature("plain") is None
    assert setup_feature("missing") is None


def test_the_category_is_the_group_unless_declared(declare):
    declare(
        Command("grouped", group="moderations"),
        Command("placed", group="moderations", category="notifications"),
    )

    assert command_for("grouped").category == "moderations"
    assert command_for("placed").category == "notifications"


def test_answering_the_prefix_is_derived_from_the_prefix_hook(declare):
    declare(
        Command("answers", hooks=Hooks(prefixed_message=_recorder([], "a", True))),
        Command("silent", hooks=Hooks(message_sent=_recorder([], "s"))),
    )

    assert command_for("answers").answers_prefix
    assert not command_for("silent").answers_prefix


def test_member_hooks_run_by_join_stage_then_in_the_declared_order(declare):
    calls: List[str] = []
    declare(
        Command("announces", hooks=Hooks(member_joined=_recorder(calls, "announces"))),
        Command("quiet"),
        Command(
            "changes",
            hooks=Hooks(
                member_joined=_recorder(calls, "changes"),
                join_stage=JoinStage.CHANGES_MEMBER,
            ),
        ),
        Command("later", hooks=Hooks(member_joined=_recorder(calls, "later"))),
    )

    assert [key for key, _hook in member_hooks()] == ["changes", "announces", "later"]


async def test_a_failing_hook_never_stops_the_next_one(declare, caplog):
    calls: List[str] = []
    declare(
        Command("broken", hooks=Hooks(member_joined=_failing(calls, "broken"))),
        Command("next", hooks=Hooks(member_joined=_recorder(calls, "next"))),
    )

    with caplog.at_level(logging.ERROR):
        async with trace.listener("on_member_join", "10", user_id="20") as work:
            await dispatch.member_joined(work, MemberJoined("10", "20", member=None))

    assert calls == ["broken", "next"]
    assert any("(broken)" in record.getMessage() for record in caplog.records), (
        "the failure is logged under the part named after the command's key"
    )


async def test_the_first_prefix_hook_that_answers_wins(declare):
    calls: List[str] = []
    declare(
        Command("declines", hooks=Hooks(prefixed_message=_recorder(calls, "d", False))),
        Command("answers", hooks=Hooks(prefixed_message=_recorder(calls, "a", True))),
        Command("never", hooks=Hooks(prefixed_message=_recorder(calls, "n", True))),
        Command("every", hooks=Hooks(message_sent=_recorder(calls, "every"))),
    )

    async def server_prefix() -> str:
        return "k!"

    async with trace.listener("on_message", "10") as work:
        await dispatch.message_sent(work, _message("k!ping"), server_prefix())

    assert calls == ["d", "a", "every"]


async def test_a_failing_prefix_hook_ends_the_prefix_chain(declare):
    calls: List[str] = []
    seen: List[MessageSent] = []

    async def every(context: MessageSent) -> None:
        seen.append(context)

    declare(
        Command("fails", hooks=Hooks(prefixed_message=_failing(calls, "f"))),
        Command("never", hooks=Hooks(prefixed_message=_recorder(calls, "n", True))),
        Command("every", hooks=Hooks(message_sent=every)),
    )

    async def server_prefix() -> str:
        return "k!"

    async with trace.listener("on_message", "10") as work:
        await dispatch.message_sent(work, _message("k!ping"), server_prefix())

    assert calls == ["f"]
    assert [context.prefix_handled for context in seen] == [True]


async def test_a_declined_prefix_leaves_the_message_unhandled(declare):
    seen: List[MessageSent] = []

    async def every(context: MessageSent) -> None:
        seen.append(context)

    declare(
        Command("declines", hooks=Hooks(prefixed_message=_recorder([], "d", False))),
        Command("every", hooks=Hooks(message_sent=every)),
    )

    async def server_prefix() -> str:
        return "k!"

    async with trace.listener("on_message", "10") as work:
        await dispatch.message_sent(work, _message("k!ping"), server_prefix())

    assert [(context.prefix, context.prefix_handled) for context in seen] == [
        ("k!", False)
    ]


async def test_a_message_off_the_prefix_reaches_only_the_message_hooks(declare):
    calls: List[str] = []
    declare(
        Command("answers", hooks=Hooks(prefixed_message=_recorder(calls, "a", True))),
        Command("every", hooks=Hooks(message_sent=_recorder(calls, "every"))),
    )

    async def server_prefix() -> str:
        return "k!"

    async with trace.listener("on_message", "10") as work:
        await dispatch.message_sent(work, _message("hello"), server_prefix())

    assert calls == ["every"]


async def test_a_prefix_that_cannot_be_read_skips_only_the_prefix_hooks(declare):
    calls: List[str] = []
    seen: List[MessageSent] = []

    async def every(context: MessageSent) -> None:
        calls.append("every")
        seen.append(context)

    declare(
        Command("answers", hooks=Hooks(prefixed_message=_recorder(calls, "a", True))),
        Command("every", hooks=Hooks(message_sent=every)),
    )

    async def unreadable() -> str:
        raise RuntimeError("settings unreachable")

    async with trace.listener("on_message", "10") as work:
        await dispatch.message_sent(work, _message("k!ping"), unreadable())

    assert calls == ["every"]
    assert [context.prefix_handled for context in seen] == [True]


async def test_a_direct_message_reaches_only_the_message_hooks(declare):
    seen: List[MessageSent] = []

    async def every(context: MessageSent) -> None:
        seen.append(context)

    declare(
        Command("answers", hooks=Hooks(prefixed_message=_recorder([], "a", True))),
        Command("every", hooks=Hooks(message_sent=every)),
    )

    async with trace.listener("on_message", None) as work:
        await dispatch.message_sent(work, _message("k!ping", guild_id=None))

    assert [context.guild_id for context in seen] == [None]
    assert prefix_hooks() and message_hooks()


async def test_edit_and_guild_join_hooks_run_in_the_declared_order(declare):
    calls: List[str] = []
    declare(
        Command(
            "first",
            hooks=Hooks(
                message_edited=_recorder(calls, "edit-1"),
                guild_joined=_recorder(calls, "join-1"),
            ),
        ),
        Command(
            "second",
            hooks=Hooks(
                message_edited=_recorder(calls, "edit-2"),
                guild_joined=_recorder(calls, "join-2"),
            ),
        ),
    )

    async with trace.listener("on_raw_message_edit", "10") as work:
        await dispatch.message_edited(work, MessageEdited("10", "30", None, None))
    async with trace.listener("on_guild_join", "10") as work:
        await dispatch.guild_joined(work, GuildJoined("10", guild=None))

    assert calls == ["edit-1", "edit-2", "join-1", "join-2"]


@pytest.mark.parametrize(
    "hook, gathered",
    [
        ("persistent_views", dispatch.persistent_views),
        ("dynamic_items", dispatch.dynamic_items),
    ],
)
def test_the_views_and_dynamic_items_of_every_command_are_gathered(
    declare, hook, gathered
):
    declare(
        Command("one", hooks=Hooks(**{hook: lambda: ["item-a", "item-b"]})),
        Command("none"),
        Command("two", hooks=Hooks(**{hook: lambda: ["item-c"]})),
    )

    assert gathered() == ("item-a", "item-b", "item-c")


def test_a_reminder_reaches_the_command_that_owns_its_title(declare):
    handled: List[Reminder] = []
    declare(Command("owner", reminders={"renewal": handled.append}))

    assert dispatch.reminder(Reminder("1", "renewal", "notes"))
    assert not dispatch.reminder(Reminder("2", "unknown"))
    assert [fired.id for fired in handled] == ["1"]


def test_a_webhook_event_reaches_the_command_that_registered_it(declare):
    def online(event: WebhookEvent) -> WebhookReply:
        return WebhookReply(200, f"{event.name} {event.subject}")

    declare(Command("owner", webhooks={"service.online": online}))

    assert dispatch.webhook(WebhookEvent("service.online", "someone")) == WebhookReply(
        200, "service.online someone"
    )
    assert dispatch.webhook(WebhookEvent("service.offline", "someone")) is None


async def test_due_schedules_reach_the_command_that_owns_their_kind(declare):
    handled: List[Any] = []

    async def due(schedules: Any) -> None:
        handled.append(list(schedules))

    declare(Command("owner", schedules={"live": due}))

    assert dispatch.schedule_kinds() == ("live",)
    assert await dispatch.schedules("live", [{"_id": "live:1"}])
    assert not await dispatch.schedules("unknown", [{"_id": "unknown:1"}])
    assert handled == [[{"_id": "live:1"}]]
