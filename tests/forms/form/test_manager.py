"""The manager: the panel's lines as an admin reads them, and what its buttons do."""

import pytest

from app.settings.form import events as ev
from app.settings.form.actions.action import Context
from app.settings.form.components import Card, Gallery, OptionSelect, Panel, Picker
from app.settings.form.effects import (
    Commit,
    Finalize,
    Notice,
    ResumeParent,
    ShowError,
)
from app.settings.form.form_state import (
    Edit,
    EditItem,
    Manage,
    Setup,
    Status,
    new_session,
)
from app.settings.form.manager import (
    Lifecycle,
    LifecycleConfirmed,
    OptionToggled,
    PanelExtras,
    RemoveItemConfirmed,
)
from app.settings.form.responses.styles import format_value
from app.settings.form.responses.summary import PanelRow, panel_groups, row_value
from tests.forms.replay import (
    NOW,
    ORIGIN,
    decided,
    evt,
    kinds,
    open_part,
    screen,
    start,
)

pytestmark = pytest.mark.unit


def _row(value, style=None):
    return PanelRow(key="k", title="Title", value=value, style=style)


def _grouped(**fields):
    return PanelRow(
        group="exceptions", group_title="Exceptions", group_declared=True, **fields
    )


def test_a_group_heading_carries_no_emoji_of_its_own():
    """Lucas: the panel emojis piled up. Every line already leads with its own
    icon, so the heading above them repeated one more."""
    rows = (
        _grouped(
            key="allowed_links",
            title="Websites",
            value=["youtube.com"],
            style="bullet",
            icon="🔗",
            target="card/allowed_links",
        ),
        _grouped(
            key="custom_links",
            title="Your links",
            value=[{}],
            style="composition",
            icon="📌",
            target="custom_links",
        ),
    )

    heading = panel_groups(rows, "Block Links", "pt-br")[0].heading

    assert heading == "### Exceptions"


def test_a_list_with_no_item_reads_on_one_line():
    """Broke as: "Your links:" and "None" came out on two lines, because an
    empty list was formatted as a block of items."""
    text, is_block = row_value(_row([], "composition"), "pt-br")

    assert (text, is_block) == ("Nada ainda", False)


def test_an_empty_list_of_a_declared_group_offers_add_instead_of_edit():
    """Lucas: the Exceptions heading showed one button for the popular sites
    and none for his own links, because an empty list has nothing to edit."""
    rows = (
        _grouped(
            key="allowed_links",
            title="Websites",
            value=[],
            style="bullet",
            icon="🔗",
            target="card/allowed_links",
            edit_label="Free a website",
        ),
        _grouped(
            key="custom_links",
            title="Your links",
            value=[],
            style="composition",
            icon="📌",
            target="custom_links",
            edit_label="Free specific links",
        ),
    )

    parts = panel_groups(rows, "Block Links", "pt-br")[0].parts

    assert [part.button for part in parts] == [
        "edit:card/allowed_links",
        "add:custom_links",
    ], "the button names the list it adds to, so its id never repeats the Add below"
    assert [part.label for part in parts] == ["Free a website", "Free specific links"]
    assert parts[1].emoji == "➕"


def test_an_empty_list_setting_reads_none_without_a_code_block():
    """Broke as: block_links with no popular site allowed showed six backticks."""
    for locale, none in (("pt-br", "Nada ainda"), ("en-us", "None")):
        for row in (
            _row({"style": "bullet", "values": []}),
            _row([], "channel"),
            _row([], "role"),
            _row([], "code"),
        ):
            text, is_block = row_value(row, locale)
            assert (text, is_block) == (none, False), row


def test_an_empty_list_formats_to_nothing_instead_of_an_empty_code_block():
    assert format_value([], "bullet", "pt-br") == ""
    assert format_value([], "numbered", "pt-br") == ""


def _links_document(count):
    from tests.behavioral.golden.paths.block_links import ENABLED

    document = dict(ENABLED)
    document["custom_links"] = {
        "style": "composition",
        "values": [
            {"link": {"value": f"site{index}.com", "title": "Link", "style": "code"}}
            for index in range(count)
        ],
    }
    return document


def _reachable(count):
    from app.settings.form.form_yaml import registry
    from app.settings.form.responses.summary import (
        every_setting_has_a_button,
        panel_groups,
        panel_rows,
    )

    definition = registry.get("block_links")
    document = _links_document(count)
    rows = panel_rows(definition, document, "pt-br")
    groups = panel_groups(rows, "Bloquear Links", "pt-br")
    return every_setting_has_a_button(definition, document, groups, "pt-br", rows)


def test_a_list_longer_than_the_screen_keeps_the_panel_edit():
    """Broke as: a heading's own screen draws only the first three items, but
    the panel counted every item of the list as covered and dropped the global
    Edit, which was the only way to reach the fourth."""
    from app.constants import ViewConstants

    shown = ViewConstants.GROUP_ITEMS_SHOWN

    assert _reachable(shown) is True, "the screen buttons all of them"
    assert _reachable(shown + 1) is False, "the panel keeps its Edit for the rest"


def test_a_boolean_stored_as_a_string_reads_as_no():
    """Broke as: a styled option persists "True"/"False" as a STRING, and the
    old formatter read "False" as truthy and showed "Sim". The guarantee moved
    here with the formatter when the old engine's copy was deleted."""
    assert format_value("False", "boolean", "pt-br") == "Não"
    assert format_value("false", "boolean", "pt-br") == "Não"
    assert format_value("True", "boolean", "pt-br") == "Sim"
    assert format_value(False, "boolean", "pt-br") == "Não"
    assert format_value(True, "boolean", "en-us") == "Yes"


def test_the_code_style_keeps_one_value_inline_and_a_list_in_a_block():
    """The `code` style renders a URL monospaced on the panel and the review:
    one value inline, several inside a block. Moved here with the formatter."""
    assert format_value("meusite.com.br", "code", "pt-br") == "`meusite.com.br`"
    listed = format_value(["a.com", "b.com"], "code", "pt-br")
    assert listed.startswith("\n```") and "a.com\nb.com" in listed


BLOCK_LINKS_DOC = {
    "guild_id": "123456789",
    "enabled": True,
    "mode": "block_all",
    "allowed_chats": {"style": "channel", "values": "100"},
    "allowed_roles": {"style": "role", "values": "201"},
    "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
    "custom_links": {
        "style": "composition",
        "values": [
            {
                "link": {
                    "value": "meusite.com.br",
                    "title": "Link ou Site",
                    "style": "code",
                }
            },
            {
                "link": {
                    "value": "docs.example.org",
                    "title": "Link ou Site",
                    "style": "code",
                }
            },
        ],
    },
    "answer": "Nada de links aqui! :p",
}


def test_the_manager_renders_the_panel_with_grouped_settings():
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    panel = screen(decision).components[0]
    assert isinstance(panel, Panel)
    assert [g.key for g in panel.groups] == [
        "link_settings",
        "permissions",
        "group:exceptions",
    ]
    assert "**Modo de bloqueio:** Bloquear todos" in panel.groups[0].lines[1]
    assert [b.action for b in screen(decision).buttons] == [
        "lifecycle:pause",
        "lifecycle:disable",
        "add",
        "remove",
        "aside:history",
        "aside:help",
    ]


def test_settings_of_different_steps_group_under_one_declared_heading():
    """The popular websites live on the card and the custom links are a list of
    their own: on the panel they read as one block, behind a single Edit, and
    the screen it opens is where each of them gets its own button."""
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    panel = screen(decision).components[0]
    group = next(g for g in panel.groups if "Exceções" in g.heading)

    assert group.key == "group:exceptions"
    assert not group.parts, "one Edit for the heading, not one per line"
    body = "\n".join(group.lines)
    assert "Sites populares permitidos" in body and "Seus Links" in body

    opened = decided(
        definition,
        decision.session,
        evt(ev.EditRequested, target="group:exceptions"),
        Context(document=BLOCK_LINKS_DOC),
    )
    view = screen(opened).components[0]
    toggles = next(group for group in view.groups if group.choices)
    assert toggles.choice_target == "allowed_links"
    assert any(group.actions for group in view.groups), "each link keeps its buttons"


def test_a_section_edit_opens_a_child_over_that_step_seeded_from_the_document():
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    opened = decided(
        definition,
        decision.session,
        evt(ev.EditRequested, target="link_settings"),
        Context(document=BLOCK_LINKS_DOC),
    )
    child = opened.effects[0].session
    assert child.mode == Edit(("link_settings",))
    assert child.raw("answer") == "Nada de links aqui! :p"
    started = decided(
        definition, child, evt(ev.Started), Context(document=BLOCK_LINKS_DOC)
    )
    assert started.session.cursor == "link_settings"
    done = decided(
        definition,
        started.session,
        evt(ev.Answered, step_key="link_settings"),
        Context(document=BLOCK_LINKS_DOC),
    )
    assert (
        isinstance(done.effects[0], ResumeParent)
        and done.effects[0].child_mode == "edit"
    )
    committed = decided(
        definition,
        opened.session,
        evt(ev.ChildFinished, child_mode="edit", answers=done.effects[0].answers),
        Context(document=BLOCK_LINKS_DOC),
    )
    assert (
        isinstance(committed.effects[0], Commit) and committed.effects[0].kind == "edit"
    )


def test_lifecycle_actions_need_the_typed_word():
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    asked = decided(definition, decision.session, evt(Lifecycle, action="pause"))
    assert kinds(asked) == ["OpenModal"] and asked.session.status is Status.AWAITING
    wrong = decided(
        definition,
        asked.session,
        evt(LifecycleConfirmed, action="pause", word="nope"),
    )
    assert kinds(wrong) == ["Ack"] and wrong.session.status is Status.ACTIVE
    asked = decided(definition, wrong.session, evt(Lifecycle, action="pause"))
    right = decided(
        definition,
        asked.session,
        evt(LifecycleConfirmed, action="pause", word="pausar"),
    )
    assert isinstance(right.effects[0], Commit) and right.effects[0].kind == "pause"
    done = decided(definition, right.session, evt(ev.CommitSucceeded, kind="pause"))
    assert done.effects[0].kind == "paused"


def test_removing_from_the_manager_commits_the_item():
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    picker = decided(
        definition,
        decision.session,
        evt(ev.RemoveRequested),
        Context(document=BLOCK_LINKS_DOC),
    )
    assert screen(picker).components[0].options[0].value == "custom_links$0"
    removed = decided(
        definition,
        picker.session,
        evt(ev.TargetChosen, value="custom_links$0"),
        Context(document=BLOCK_LINKS_DOC),
    )
    commit = removed.effects[0]
    assert isinstance(commit, Commit) and commit.kind == "remove_item"
    assert commit.payload["item"]["link"]["value"] == "meusite.com.br"


def test_back_on_the_edit_dropdown_redraws_the_panel():
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    picker = decided(
        definition,
        decision.session,
        evt(ev.EditRequested, target="custom_links"),
        Context(document=BLOCK_LINKS_DOC),
    )
    assert [b.action for b in screen(picker).buttons] == ["picker_back"]
    back = decided(
        definition,
        picker.session,
        evt(ev.PickerClosed),
        Context(document=BLOCK_LINKS_DOC),
    )
    assert back.session.status is Status.ACTIVE
    assert isinstance(screen(back).components[0], Panel)


def test_asides_never_touch_the_session():
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    aside = decided(definition, decision.session, evt(ev.Aside, name="history"))
    assert kinds(aside) == ["RunAside"]
    assert aside.session.revision == decision.session.revision


def test_the_panel_intro_prefers_the_manager_description():
    """Broke as: the panel read "Enabling this feature allows me to..." on a
    feature that was already on."""
    import copy

    from app.settings.form.form_yaml import DiskSource, compile_form

    raw = copy.deepcopy(DiskSource().load("block_links"))
    raw["steps"][0]["manager_description"] = {
        "en-us": "I keep an eye on links.",
        "pt-br": "Estou de olho nos links.",
    }
    definition = compile_form("block_links", raw)

    def opened(mode, context):
        session = new_session(
            (definition.key, definition.version), mode, ORIGIN, ttl_seconds=60, now=NOW
        )
        return screen(decided(definition, session, evt(ev.Started), context))

    panel = opened(Manage(), Context(document=BLOCK_LINKS_DOC)).components[0]
    assert panel.intro == "Estou de olho nos links."
    intro = opened(Setup(), None)
    assert "Estou de olho nos links." not in (intro.description or "")


BIRTHDAY_DOC = {
    "guild_id": "123456789",
    "enabled": True,
    "reminders_birthday": {
        "style": "composition",
        "values": [{"user": {"value": "777", "title": "Membro", "style": "user"}}],
    },
}


def test_edit_beside_a_list_kept_past_its_gate_opens_that_list():
    """Broke as: a list whose gate said "later" was kept on the panel once it
    held something, but the Edit beside it opened nothing, because the picker
    still dropped the step its gate refused."""
    document = {**BLOCK_LINKS_DOC, "add_custom": "false"}
    definition, decision = start(
        "block_links", Manage(), context=Context(document=document)
    )
    opened = decided(
        definition,
        decision.session,
        evt(ev.EditRequested, target="custom_links"),
        Context(document=document),
    )
    picker = screen(opened).components[0]
    assert isinstance(picker, OptionSelect)
    assert [option.value for option in picker.options] == [
        "custom_links$0",
        "custom_links$1",
    ]


def test_edit_beside_a_member_keyed_list_opens_the_member_picker():
    """Broke as: the birthday list's own Edit drew a dropdown with no option,
    which Discord refuses."""
    definition, decision = start(
        "reminders_birthday", Manage(), context=Context(document=BIRTHDAY_DOC)
    )
    opened = decided(
        definition,
        decision.session,
        evt(ev.EditRequested, target="reminders_birthday"),
        Context(document=BIRTHDAY_DOC),
    )
    picker = screen(opened).components[0]
    assert isinstance(picker, Picker) and picker.slot == "member"
    assert opened.session.awaiting == "member:edit"


STREAM_ELEMENTS_DOC = {"guild_id": "123456789", "enabled": True, "streamer": "shroud"}


def _panel(form, document, panel_rows=None):
    context = Context(document=document)
    panel = PanelExtras(rows=panel_rows)
    definition, decision = start(form, Manage(), context=context, panel=panel)
    return screen(decision), screen(decision).components[0]


def test_every_visible_step_is_a_panel_group_with_its_own_edit():
    """Broke as: a single step (a channel, a modal) had no Edit of its own, only
    the global Edit and a dropdown to pick the step."""
    from tests.behavioral.golden.paths.welcome_messages import ENABLED

    drawn, panel = _panel("stream_elements_commands", STREAM_ELEMENTS_DOC)
    assert [g.key for g in panel.groups] == ["streamer"]
    assert "edit" not in [b.action for b in drawn.buttons]

    drawn, panel = _panel("welcome_messages", ENABLED)
    [group] = panel.groups
    assert group.key == "welcome_config"
    assert [part.target for part in group.parts] == [
        "welcome_config/channel",
        "welcome_config/design",
        "welcome_config/messages",
    ]
    assert "edit" not in [b.action for b in drawn.buttons]


def test_every_panel_line_leads_with_its_icon_or_the_frisbee():
    _drawn, panel = _panel("block_links", BLOCK_LINKS_DOC)
    lines = [line for group in panel.groups for line in group.lines]
    mode = next(line for line in lines if "**Modo de bloqueio:**" in line)
    assert mode.startswith("🚦 ")
    chats = next(line for line in lines if "**Canais Liberados:**" in line)
    assert chats.startswith("#️⃣ ")


def test_a_one_line_group_has_no_heading():
    _drawn, panel = _panel("stream_elements_commands", STREAM_ELEMENTS_DOC)
    [group] = panel.groups
    assert group.heading == ""
    assert len(group.lines) == 1 and "**" in group.lines[0]
    assert not group.lines[0].startswith("**")


def test_feature_rows_group_by_the_step_that_owns_their_key():
    from app.settings.form.responses.summary import PanelRow

    rows = (
        PanelRow(key="channel", title="Canal", value="100", style="channel"),
        PanelRow(key="timezone", title="Fuso", value="America/Sao_Paulo"),
        PanelRow(key="reminders_birthday", title="Total", value="3"),
    )
    drawn, panel = _panel("reminders_birthday", {"guild_id": "1"}, rows)
    assert [g.key for g in panel.groups] == ["birthday_config", "reminders_birthday"]
    assert "edit" not in [b.action for b in drawn.buttons]


def test_feature_rows_without_keys_keep_the_global_edit():
    from app.settings.form.responses.summary import PanelRow

    rows = (PanelRow(key="", title="Total", value="3"),)
    drawn, _panel_component = _panel("reminders_birthday", {"guild_id": "1"}, rows)
    assert "edit" in [b.action for b in drawn.buttons]


def _part_manager():
    from tests.forms.form.card_fixtures import WELCOME_LIKE_DOC, welcome_like_card

    definition = welcome_like_card(edit_by_field=True)
    session = new_session(
        (definition.key, definition.version), Manage(), ORIGIN, ttl_seconds=60, now=NOW
    )
    context = Context(document=WELCOME_LIKE_DOC)
    panel = decided(definition, session, evt(ev.Started), context)
    return definition, panel.session, context


def test_the_exceptions_are_one_block_that_opens_a_screen_of_its_own():
    """Lucas: the two exception buttons crowded the panel and only one of them
    worked. The heading now holds one Edit, and the screen it opens carries the
    popular websites as buttons, one block per link with its own Edit and
    Remove, and a single way to add another."""
    from tests.behavioral.golden.paths.block_links import ENABLED

    context = Context(document=ENABLED)
    definition, panel = start("block_links", Manage(), context=context)

    groups = screen(panel).components[0].groups
    assert groups[-1].key == "group:exceptions", [group.key for group in groups]
    assert not groups[-1].parts, "the heading holds one Edit, not one per line"

    opened = decided(
        definition,
        panel.session,
        evt(ev.EditRequested, target="group:exceptions"),
        context,
    )

    shown = screen(opened)
    actions = [button.action for button in shown.buttons]
    assert actions == ["add:custom_links", "picker_back"], actions
    view = shown.components[0]
    assert view.intro, "the screen says what the heading is for"
    assert any(group.choices for group in view.groups), "the websites are buttons here"
    beside_items = [button.action for group in view.groups for button in group.actions]
    assert beside_items == [
        "edit:custom_links$0",
        "remove_one:custom_links$0",
        "edit:custom_links$1",
        "remove_one:custom_links$1",
    ], beside_items


EXCEPTIONS_DOC = {
    **BLOCK_LINKS_DOC,
    "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
}


def _exceptions_screen(document):
    context = Context(document=document)
    definition, panel = start("block_links", Manage(), context=context)
    opened = decided(
        definition,
        panel.session,
        evt(ev.EditRequested, target="group:exceptions"),
        context,
    )
    return definition, opened, context


def test_a_popular_website_turns_on_where_it_is_read():
    """Lucas: a screen with nothing but a dropdown does not even show that more
    than one can be picked. The websites are buttons on the screen itself, and
    turning one on saves without taking the screen away."""
    definition, opened, context = _exceptions_screen(EXCEPTIONS_DOC)
    toggles = next(g for g in screen(opened).components[0].groups if g.choices)
    assert [c.selected for c in toggles.choices if c.value == "youtube.com"] == [True]

    turned = decided(
        definition,
        opened.session,
        evt(OptionToggled, target="allowed_links", value="spotify.com", turned_on=True),
        context,
    )

    written = next(e for e in turned.effects if isinstance(e, Commit))
    assert written.kind == "toggle" and written.quiet
    assert written.payload == {
        "key": "allowed_links",
        "value": "spotify.com",
        "turned_on": True,
    }, "the commit carries the state the screen asked for"
    saved = decided(
        definition, turned.session, evt(ev.CommitSucceeded, kind="toggle"), context
    )
    assert kinds(saved) == ["Render"], "no success message, the screen stays"
    assert "Exceções" in screen(saved).components[0].title


def test_removing_one_link_asks_first_and_stays_on_the_screen():
    """Lucas: the Remove sits under each item here, and answering it should not
    throw the admin back to the panel."""
    definition, opened, context = _exceptions_screen(BLOCK_LINKS_DOC)

    asked = decided(
        definition,
        opened.session,
        evt(ev.RemoveRequested, target="custom_links$0"),
        context,
    )
    assert kinds(asked) == ["Confirm"]

    removed = decided(
        definition,
        asked.session,
        evt(RemoveItemConfirmed, target="custom_links$0"),
        context,
    )
    written = next(e for e in removed.effects if isinstance(e, Commit))
    assert written.kind == "remove_item" and written.quiet

    saved = decided(
        definition,
        removed.session,
        evt(ev.CommitSucceeded, kind="remove_item"),
        context,
    )
    assert "Exceções" in screen(saved).components[0].title, "the screen stays"


def _refused_on_the_exceptions_screen(error, document=BLOCK_LINKS_DOC):
    definition, opened, context = _exceptions_screen(BLOCK_LINKS_DOC)
    asked = decided(
        definition,
        opened.session,
        evt(ev.RemoveRequested, target="custom_links$0"),
        context,
    )
    removed = decided(
        definition,
        asked.session,
        evt(RemoveItemConfirmed, target="custom_links$0"),
        context,
    )
    return decided(
        definition,
        removed.session,
        evt(ev.CommitFailed, kind="remove_item", error=error),
        Context(document=document),
    )


@pytest.mark.parametrize(
    "error, answer",
    [
        ("stale", Notice("stale")),
        ("duplicate", ShowError("item-already-registered")),
        ("RuntimeError", ShowError("command-generic-error")),
    ],
)
def test_a_refused_change_on_a_heading_screen_answers_in_copy_and_redraws_it(
    error, answer
):
    """Broke as: the exceptions screen answered a refused change with
    `ShowError(<reason>)`, whose copy keys do not exist, so the admin read
    `errors.stale.title` over the old list."""
    refused = _refused_on_the_exceptions_screen(error)

    assert refused.effects[0] == answer
    assert kinds(refused)[1:] == ["Render"]
    assert "Exceções" in screen(refused).components[0].title
    assert refused.session.status is Status.ACTIVE
    assert refused.analytics == (), "the session goes on, so no failed commit ends it"


def test_a_change_refused_once_the_feature_is_gone_closes_the_form_in_place():
    refused = _refused_on_the_exceptions_screen("stale", document={})

    assert refused.effects == (Finalize("closed"),)
    assert refused.session.status is Status.CANCELLED
    assert refused.analytics == (), "a refusal is not a failed commit"


def test_a_stale_change_from_the_panel_redraws_the_panel_and_keeps_it_open():
    definition, panel = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    picker = decided(
        definition,
        panel.session,
        evt(ev.RemoveRequested),
        Context(document=BLOCK_LINKS_DOC),
    )
    removed = decided(
        definition,
        picker.session,
        evt(ev.TargetChosen, value="custom_links$0"),
        Context(document=BLOCK_LINKS_DOC),
    )
    refused = decided(
        definition,
        removed.session,
        evt(ev.CommitFailed, kind="remove_item", error="stale"),
        Context(document=BLOCK_LINKS_DOC),
    )

    assert kinds(refused) == ["Notice", "Render"]
    assert isinstance(screen(refused).components[0], Panel)
    assert refused.session.status is Status.ACTIVE


def test_back_from_the_exceptions_screen_returns_to_the_panel():
    definition, opened, context = _exceptions_screen(BLOCK_LINKS_DOC)

    closed = decided(definition, opened.session, evt(ev.PickerClosed), context)

    assert closed.session.cursor is None
    assert "Bloquear Links" in screen(closed).components[0].title


def test_the_edit_beside_a_declared_group_opens_that_part():
    """Broke as: the two buttons of the block links "Exceptions" heading did
    nothing at all. The panel gives a card field its own Edit whenever the
    field declares a panel group of its own, but the engine only honoured a
    part target on a card that also declares `edit_by_field`, so the click
    answered a stale notice and redrew the panel."""
    from tests.behavioral.golden.paths.block_links import ENABLED

    context = Context(document=ENABLED)
    definition, panel = start("block_links", Manage(), context=context)
    opened = decided(
        definition,
        panel.session,
        evt(ev.EditRequested, target="link_settings/allowed_links"),
        context,
    )

    assert kinds(opened) == ["OpenChild"], kinds(opened)
    child = opened.effects[0].session
    assert child.mode == Edit(("link_settings",), part="allowed_links")


def test_a_part_edit_opens_only_that_section_picker():
    definition, parent, context = _part_manager()
    opened, started = open_part(definition, parent, context, "welcome_card/channel")

    assert opened.effects[0].session.mode == Edit(("welcome_card",), part="channel")
    picker = screen(started).components[0]
    assert isinstance(picker, Picker) and picker.slot == "channel"
    assert started.session.awaiting == "section:0"


def test_a_part_edit_modal_is_the_first_answer_to_the_click():
    definition, parent, context = _part_manager()
    _opened, started = open_part(definition, parent, context, "welcome_card/messages")

    assert kinds(started) == ["OpenModal"]


def test_choosing_in_a_part_edit_commits_the_edit_from_the_manager():
    definition, parent, context = _part_manager()
    opened, started = open_part(definition, parent, context, "welcome_card/channel")
    chosen = decided(
        definition,
        started.session,
        evt(ev.Drafted, step_key="welcome_card", changes={"channel": ["101"]}),
        context,
    )
    handed = next(e for e in chosen.effects if isinstance(e, ResumeParent))
    assert handed.answers["channel"].raw == "101"

    committed = decided(
        definition,
        opened.session,
        evt(ev.ChildFinished, child_mode="edit", answers=handed.answers),
        context,
    )
    commit = committed.effects[0]
    assert isinstance(commit, Commit) and commit.kind == "edit"
    assert commit.payload["answers"]["channel"].raw == "101"


def test_back_from_a_part_edit_redraws_the_panel_without_committing():
    definition, parent, context = _part_manager()
    opened, started = open_part(definition, parent, context, "welcome_card/channel")
    closed = decided(definition, started.session, evt(ev.PickerClosed), context)
    handed = next(e for e in closed.effects if isinstance(e, ResumeParent))
    assert handed.cancelled

    back = decided(
        definition,
        opened.session,
        evt(ev.ChildFinished, child_mode="edit", answers={}, cancelled=True),
        context,
    )
    assert not [e for e in back.effects if isinstance(e, Commit)]
    assert isinstance(screen(back).components[0], Panel)


def test_a_part_edit_that_leaves_the_card_incomplete_shows_the_whole_card():
    definition, parent, context = _part_manager()
    _opened, started = open_part(definition, parent, context, "welcome_card/design")
    assert isinstance(screen(started).components[0], Gallery)

    chosen = decided(
        definition,
        started.session,
        evt(ev.Answered, step_key="section:1", payload="custom_only"),
        context,
    )
    assert "ShowError" in kinds(chosen)
    assert not [e for e in chosen.effects if isinstance(e, ResumeParent)]
    assert isinstance(screen(chosen).components[0], Card)


def test_a_part_edit_of_a_multi_select_shows_only_that_select():
    import copy

    from app.settings.form.form_yaml import DiskSource, compile_form
    from tests.behavioral.golden.paths.default_roles import ENABLED

    raw = copy.deepcopy(DiskSource().load("default_roles"))
    raw["steps"][1]["edit_by_field"] = True
    definition = compile_form("default_roles", raw)
    session = new_session(
        (definition.key, definition.version), Manage(), ORIGIN, ttl_seconds=60, now=NOW
    )
    context = Context(document=ENABLED)
    parent = decided(definition, session, evt(ev.Started), context).session
    _opened, started = open_part(
        definition, parent, context, "default_roles_config/default_roles"
    )

    drawn = screen(started)
    assert [c.slot for c in drawn.components] == ["default_roles"]
    assert [b.action for b in drawn.buttons] == ["picker_back"]


def test_a_second_edit_after_a_dismissed_modal_opens_a_fresh_child():
    definition, parent, context = _part_manager()
    first = decided(
        definition,
        parent,
        evt(ev.EditRequested, target="welcome_card/messages"),
        context,
    )
    second = decided(
        definition,
        first.session,
        evt(ev.EditRequested, target="welcome_card/messages"),
        context,
    )
    assert kinds(second) == ["OpenChild"]


def _twitch_by_item():
    import copy

    from app.settings.form.form_yaml import DiskSource, compile_form

    raw = copy.deepcopy(DiskSource().load("notifications_twitch"))
    raw["steps"][1]["edit_by_item"] = True
    return compile_form("notifications_twitch", raw)


def _twitch_panel():
    from tests.behavioral.golden.paths.notifications_twitch import ENABLED

    definition = _twitch_by_item()
    session = new_session(
        (definition.key, definition.version), Manage(), ORIGIN, ttl_seconds=60, now=NOW
    )
    context = Context(document=ENABLED)
    return definition, decided(definition, session, evt(ev.Started), context), context


def test_edit_beside_an_item_opens_that_item():
    definition, panel, context = _twitch_panel()

    opened = decided(
        definition,
        panel.session,
        evt(ev.EditRequested, target="notifications$1"),
        context,
    )

    child = opened.effects[0].session
    assert child.mode == EditItem(1)
    assert child.raw("streamer") == "cellbit"


def test_a_composition_edited_by_item_has_one_group_per_item():
    _definition, panel, _context = _twitch_panel()

    groups = screen(panel).components[0].groups
    assert [g.key for g in groups] == ["notifications$0", "notifications$1"]
    assert groups[0].heading.endswith("#1") and groups[1].heading.endswith("#2")
    assert any("gaules" in line for line in groups[0].lines)
    assert "edit" not in [b.action for b in screen(panel).buttons]


EMPTY_LINKS_DOC = {
    **BLOCK_LINKS_DOC,
    "custom_links": {"style": "composition", "values": []},
}


def test_a_list_with_no_items_is_never_opened_by_an_edit():
    """Broke as: the Edit of an empty list built a dropdown with no option and
    Discord refused the message (50035 Invalid Form Body). The empty list keeps
    a button of its own when its heading is shared with another setting, but
    that button adds, so nothing ever opens a picker with nothing to pick."""
    context = Context(document=EMPTY_LINKS_DOC)
    definition, panel = start("block_links", Manage(), context=context)
    opened = decided(
        definition,
        panel.session,
        evt(ev.EditRequested, target="group:exceptions"),
        context,
    )

    shown = screen(opened)
    view = shown.components[0]
    parts = [part for group in view.groups for part in group.parts]
    assert not [part for part in parts if "custom_links" in part.button], (
        "there is no item to edit yet"
    )
    lines = [line for group in view.groups for line in group.lines]
    assert any("Seus Links" in line and "Nada ainda" in line for line in lines), lines
    assert "add:custom_links" in [button.action for button in shown.buttons]


def test_an_edit_with_nothing_to_pick_redraws_the_panel():
    definition, decision = start(
        "block_links", Manage(), context=Context(document=EMPTY_LINKS_DOC)
    )

    asked = decided(
        definition,
        decision.session,
        evt(ev.EditRequested, target="custom_links"),
        Context(document=EMPTY_LINKS_DOC),
    )

    drawn = screen(asked)
    assert isinstance(drawn.components[0], Panel), "no picker without options"
    assert not [
        component
        for component in drawn.components
        if isinstance(component, OptionSelect) and not component.options
    ]
