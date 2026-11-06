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
    FoundItem,
    FromSetup,
    ItemRemovalRequested,
    Lifecycle,
    LifecycleConfirmed,
    OptionToggled,
    PageTurned,
    PanelExtras,
    PanelPage,
    SearchRequested,
)
from app.settings.form.responses.styles import empty_value, format_value
from app.settings.form.responses.summary import (
    PanelRow,
    panel_groups,
    row_value,
    without_leading_emoji,
)
from tests.forms.replay import (
    NOW,
    ORIGIN,
    REGISTRY,
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

    assert (text, is_block) == (empty_value("pt-br"), False)


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
            composition=_block_links_list(),
        ),
    )

    parts = panel_groups(rows, "Block Links", "pt-br")[0].parts

    assert [part.button for part in parts] == [
        "edit:card/allowed_links",
        "add:custom_links",
    ], "the button names the list it adds to, so its id never repeats another"
    assert [part.label for part in parts] == ["Free a website", "Adicionar"]
    assert parts[1].emoji == "➕" and not parts[1].actions, "nothing to remove yet"


def _block_links_list():
    from app.settings.form.form_yaml import registry

    return registry.get("block_links").composition


def test_an_empty_list_setting_reads_none_without_a_code_block():
    """Broke as: block_links with no popular site allowed showed six backticks."""
    for locale, none in (("pt-br", "Em branco"), ("en-us", "Empty")):
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
    one value inline, several as a bullet list (P4, beta.2), never a code block.
    Moved here with the formatter."""
    assert format_value("meusite.com.br", "code", "pt-br") == "`meusite.com.br`"
    listed = format_value(["a.com", "b.com"], "code", "pt-br")
    assert listed == "\n- `a.com`\n- `b.com`"
    assert "```" not in format_value(["a", "b"], "bullet", "pt-br")


def test_message_texts_read_in_a_code_block_and_names_as_bullets():
    """R2 (beta.2 round 2): a list of message texts reads inside a code block
    of `• ` lines again, one or several, while a list of names keeps its
    Markdown bullets."""
    expected = "\n```• Oi {user}\n• Olá```"
    assert format_value(["Oi {user}", "Olá"], "code-block", "pt-br") == expected
    assert format_value("Oi {user}; Olá", "code-block", "pt-br") == expected
    assert format_value(["Oi"], "code-block", "pt-br") == "\n```• Oi```"
    assert format_value(["a.com", "b.com"], "bullet", "pt-br") == "\n- a.com\n- b.com"


def _panel_text(panel) -> str:
    lines = [panel.intro]
    for group in panel.groups:
        lines.extend((group.heading, *group.lines))
        for part in group.parts:
            lines.extend((*part.lines, *part.body))
    return "\n".join(lines)


def test_a_saved_list_of_messages_reads_in_a_code_block_on_the_panel():
    """R2: a document saved before the messages had their own style keeps the
    old `bullet` style; the panel reads the style the form declares, so the
    messages of every item come in a code block and the names stay bullets."""
    from tests.behavioral.golden.paths.notifications_twitch import ENABLED, MESSAGE

    _definition, panel = start(
        "notifications_twitch", Manage(), context=Context(document=ENABLED)
    )
    drawn = _panel_text(screen(panel).components[0])

    assert f"```• {MESSAGE}```" in drawn, drawn
    assert f"- {MESSAGE}" not in drawn, drawn


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


def test_the_panel_a_saved_setup_opens_is_the_panel_and_reports_no_opening():
    """The setup already told its story: the panel it hands over to adds no event."""
    context = Context(document=BLOCK_LINKS_DOC)
    definition, opened = start("block_links", Manage(), context=context)
    fresh = new_session(
        (definition.key, definition.version), Manage(), ORIGIN, ttl_seconds=60, now=NOW
    )

    handed = decided(definition, fresh, evt(FromSetup), context)

    assert screen(handed) == screen(opened)
    assert [name for name, _ in opened.analytics] == ["feature.manager_opened"]
    assert handed.analytics == ()
    assert handed.session.changes_saved == 1, "it never ends as an abandoned setup"


def test_the_manager_renders_the_panel_with_grouped_settings():
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    panel = screen(decision).components[0]
    assert isinstance(panel, Panel)
    assert [g.key for g in panel.groups] == ["link_settings", None]
    assert [part.button for part in panel.groups[1].parts] == [
        "edit:group:exceptions",
        "add:custom_links",
    ]
    assert "**Modo de bloqueio:** Bloquear todos" in panel.groups[0].lines[1]
    assert [b.action for b in screen(decision).buttons] == [
        "lifecycle:pause",
        "lifecycle:disable",
        "aside:history",
        "aside:help",
    ], "the list carries its own Add and Remove inside the card"


def test_the_block_links_lists_are_named_after_the_mode_in_force():
    """bl-4 (beta.2): blocking all, the custom links are the allowed ones under
    "Exceções"; blocking some, they are the blocked ones under "Links
    bloqueados", and the edit and remove pickers name them the same way."""
    from app.settings.form.manager import group_screen
    from app.settings.form.responses.summary import edit_options, remove_options

    blocking_some = {**BLOCK_LINKS_DOC, "mode": "allow_all"}
    definition, decision = start(
        "block_links", Manage(), context=Context(document=blocking_some)
    )
    panel = screen(decision).components[0]
    group = next(g for g in panel.groups if "Links bloqueados" in g.heading)
    body = "\n".join(line for part in group.parts for line in part.lines)

    opened = group_screen(definition, blocking_some, "pt-br", "exceptions")
    assert opened.components[0].title == "✅ Links bloqueados"
    assert "**Links personalizados bloqueados:**" in body, body
    assert "Sites populares" not in body
    edited = edit_options(definition, blocking_some, "pt-br")
    listed = [option.label for option in edited if "$" in option.value]
    assert listed[0] == "Links personalizados bloqueados #1", listed
    removed = remove_options(definition, blocking_some, "pt-br")
    assert [option.label for option in removed] == [
        "meusite.com.br",
        "docs.example.org",
    ], "Remove lists the links themselves (P4)"


def test_settings_of_different_steps_group_under_one_declared_heading():
    """The popular websites live on the card and the custom links are a list of
    their own: on the panel they read as one block, behind a single Edit, and
    the screen it opens is where each of them gets its own button."""
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    panel = screen(decision).components[0]
    group = next(g for g in panel.groups if "Exceções" in g.heading)

    heading, custom = group.parts
    assert heading.button == "edit:group:exceptions"
    assert "Sites populares permitidos" in "\n".join(heading.lines), (
        "one Edit for the heading's settings"
    )
    assert custom.button == "add:custom_links", "the list adds where it is drawn"
    assert "Links personalizados permitidos" in custom.lines[0]
    assert custom.body == ("- `meusite.com.br`", "- `docs.example.org`")

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
    select = screen(picker).components[0]
    assert select.options[0].value == "custom_links$0"
    assert select.max_values == 2, "an optional list may lose every item"
    chosen = decided(
        definition,
        picker.session,
        evt(ev.TargetsChosen, values=("custom_links$0", "custom_links$1")),
        Context(document=BLOCK_LINKS_DOC),
    )
    assert "- `meusite.com.br`\n- `docs.example.org`" in screen(chosen).description
    removed = decided(
        definition,
        chosen.session,
        evt(ev.RemovalConfirmed),
        Context(document=BLOCK_LINKS_DOC),
    )
    commit = removed.effects[0]
    assert isinstance(commit, Commit) and commit.kind == "remove_item"
    assert commit.payload["indexes"] == [0, 1]
    assert [item["link"]["value"] for item in commit.payload["items"]] == [
        "meusite.com.br",
        "docs.example.org",
    ]


def test_a_removal_that_no_longer_matches_the_list_redraws_it():
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    picker = decided(
        definition,
        decision.session,
        evt(ev.RemoveRequested),
        Context(document=BLOCK_LINKS_DOC),
    )
    shorter = {
        **BLOCK_LINKS_DOC,
        "custom_links": {
            "style": "composition",
            "values": BLOCK_LINKS_DOC["custom_links"]["values"][:1],
        },
    }
    chosen = decided(
        definition,
        picker.session,
        evt(ev.TargetsChosen, values=("custom_links$1",)),
        Context(document=shorter),
    )
    assert kinds(chosen) == ["Notice", "Render"]
    assert not [effect for effect in chosen.effects if isinstance(effect, Commit)]


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
    chats = next(line for line in lines if "**Canais permitidos:**" in line)
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
        PanelRow(key="reminders_birthday", title="Próximos", value="<@1>"),
    )
    listed = {
        "guild_id": "1",
        "reminders_birthday": {
            "style": "composition",
            "values": [{"user": {"value": "1", "style": "user"}}],
        },
    }
    drawn, panel = _panel("reminders_birthday", listed, rows)
    summary, *_lists, footer_group = panel.groups
    assert summary.key is None, "Pesquisar replaces the Edit of the list (bd-6)"
    assert summary.heading == "### Resumo dos Lembretes"
    assert not any("Canal" in line for line in summary.lines), "Configurar holds them"
    footer = [button.action for button in footer_group.actions]
    assert footer == ["search", "edit:group:settings"], (
        "the settings open from Configurar, the list from Pesquisar"
    )
    assert "edit" not in [b.action for b in drawn.buttons]


def test_an_empty_list_offers_no_search():
    """Round 3 B: Pesquisar has nothing to find while the list is empty, the
    way Editar is hidden over an empty list (bd-4); Configurar stays."""
    from app.settings.form.responses.summary import PanelRow

    rows = (PanelRow(key="channel", title="Canal", value="100", style="channel"),)
    drawn, panel = _panel("reminders_birthday", {"guild_id": "1"}, rows)

    actions = [button.action for group in panel.groups for button in group.actions] + [
        button.action for button in drawn.buttons
    ]
    assert "search" not in actions
    assert "edit:group:settings" in actions


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
    assert [part.button for part in groups[-1].parts] == [
        "edit:group:exceptions",
        "add:custom_links",
    ], "the heading holds one Edit, the list its own Add"

    opened = decided(
        definition,
        panel.session,
        evt(ev.EditRequested, target="group:exceptions"),
        context,
    )

    shown = screen(opened)
    actions = [button.action for button in shown.buttons]
    assert actions == ["picker_back"], actions
    view = shown.components[0]
    assert view.intro, "the screen says what the heading is for"
    assert any(group.choices for group in view.groups), "the websites are buttons here"
    heads = [part.button for group in view.groups for part in group.parts]
    assert heads == ["add:custom_links"], "Add beside the list's title"
    beside_items = [button.action for group in view.groups for button in group.actions]
    assert beside_items == [
        "edit:custom_links$0",
        "edit:custom_links$1",
        "remove:custom_links",
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


def test_a_heading_screen_draws_the_first_items_of_its_list_with_their_edit():
    """The heading's own screen builds its item blocks with the panel's
    (`item_groups`, review K1 item 11): the list's title with Add, one block per
    item with no heading of its own, the first `GROUP_ITEMS_SHOWN` of them, and
    a line counting the rest."""
    from app.constants import ViewConstants

    shown = ViewConstants.GROUP_ITEMS_SHOWN
    _definition, opened, _context = _exceptions_screen(_links_document(shown + 1))
    groups = screen(opened).components[0].groups

    items = [group for group in groups if group.keys]
    assert [group.keys for group in items] == [
        (f"custom_links${index}",) for index in range(shown)
    ]
    assert all(group.heading == "" for group in items)
    assert [button.action for button in items[0].actions] == ["edit:custom_links$0"]
    assert any(group.lines == ("e mais 1",) for group in groups)
    assert any(
        part.action == "add:custom_links" for group in groups for part in group.parts
    )


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
        evt(ItemRemovalRequested, target="custom_links$0"),
        context,
    )
    assert kinds(asked) == ["Render"]
    assert screen(asked).title == "🗑️ Remover este item?"

    removed = decided(definition, asked.session, evt(ev.RemovalConfirmed), context)
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
        evt(ItemRemovalRequested, target="custom_links$0"),
        context,
    )
    removed = decided(definition, asked.session, evt(ev.RemovalConfirmed), context)
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
    assert kinds(chosen) == ["Render", "ShowError"]
    assert chosen.session.refused.missing == ("image",)
    assert not [e for e in chosen.effects if isinstance(e, ResumeParent)]
    assert isinstance(screen(chosen).components[0], Card)


def test_a_card_edited_from_the_panel_confirms_and_a_setup_card_moves_on():
    """C3 (beta.2 round 2): a card's Done that saves, with nothing after it,
    reads Confirmar; in the setup, where the summary follows, it reads Avançar."""
    from app.settings.form.form_state import Setup
    from tests.forms.form.card_fixtures import welcome_like_card

    definition, parent, context = _part_manager()
    _opened, started = open_part(definition, parent, context, "welcome_card/design")
    whole = decided(
        definition,
        started.session,
        evt(ev.Answered, step_key="section:1", payload="custom_only"),
        context,
    )
    done = next(button for button in screen(whole).buttons if button.action == "done")
    assert done.label == "Confirmar"

    setup = welcome_like_card()
    session = new_session(
        (setup.key, setup.version), Setup(), ORIGIN, ttl_seconds=60, now=NOW
    ).at("welcome_card")
    drawn = decided(setup, session, evt(ev.ScreenRequested))
    done = next(button for button in screen(drawn).buttons if button.action == "done")
    assert done.label == "Avançar"


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

    head, first, second = screen(panel).components[0].groups
    assert [part.button for part in head.parts] == ["add:notifications"]
    assert first.heading.endswith("#1") and second.heading.endswith("#2")
    assert [button.action for button in first.actions] == [
        "edit:notifications$0",
        "aside:preview:notifications$0",
        "remove_this:notifications$0",
    ], "Edit, Preview and Remove beside each item (N5, no pause since round 3)"
    assert first.actions[-1].style == "danger"
    assert any("gaules" in line for line in first.lines)
    actions = [b.action for b in screen(panel).buttons]
    assert "edit" not in actions and "add" not in actions and "remove" not in actions


def _twitch_items(*items):
    from tests.behavioral.golden.paths.notifications_twitch import ENABLED

    return {**ENABLED, "notifications": {"style": "composition", "values": list(items)}}


def _twitch_panel_of(document):
    definition = REGISTRY.get("notifications_twitch")
    context = Context(document=document)
    _definition, panel = start("notifications_twitch", Manage(), context=context)
    return definition, panel, context


def test_an_item_saved_by_the_old_pause_reads_like_any_other():
    """N1 (round 3): the per-item pause is gone, and an item a dev panel saved
    with `enabled: false` loads as a plain item: no "(Pausado)", no Retomar,
    no line for the field, and editing it seeds no answer from it."""
    from tests.behavioral.golden.paths.notifications_twitch import notification

    paused = {**notification("100", "cellbit"), "enabled": False}
    document = _twitch_items(notification("100", "gaules"), paused)
    definition, panel, context = _twitch_panel_of(document)

    _head, _first, second = screen(panel).components[0].groups
    assert second.heading == "### Notificações #2"
    assert [button.action for button in second.actions] == [
        "edit:notifications$1",
        "aside:preview:notifications$1",
        "remove_this:notifications$1",
    ]
    assert not any("enabled" in line.casefold() for line in second.lines)

    opened = decided(
        definition,
        panel.session,
        evt(ev.EditRequested, target="notifications$1"),
        context,
    )
    child = opened.effects[0].session
    assert child.raw("streamer") == "cellbit"
    assert "enabled" not in child.answers


def test_remove_beside_an_item_names_it_and_takes_it_on_confirm():
    definition, panel, context = _twitch_panel()

    asked = decided(
        definition,
        panel.session,
        evt(ItemRemovalRequested, target="notifications$1"),
        context,
    )
    asking = screen(asked)
    assert asking.title == "🗑️ Remover este item?"
    assert "- cellbit" in asking.description
    assert [button.action for button in asking.buttons] == [
        "remove_items",
        "picker_back",
    ]

    confirmed = decided(definition, asked.session, evt(ev.RemovalConfirmed), context)
    [commit] = [effect for effect in confirmed.effects if isinstance(effect, Commit)]
    assert commit.kind == "remove_item"
    assert commit.payload["indexes"] == [1]
    assert commit.payload["items"][0]["streamer"]["value"] == "cellbit"


ONLY_ITEM_WARNINGS = [
    (
        "notifications_twitch",
        "pt-br",
        "⚠️ Ei, espere! Leia abaixo primeiro",
        "Você só tem **1** streamer configurado.",
        "Se você removê-lo agora, a função **Twitch Lives** será **desativada** e "
        "você vai precisar configurá-la de novo. Tem certeza de que quer continuar?",
    ),
    (
        "notifications_twitch",
        "en-us",
        "⚠️ Hey, wait! Read this first",
        "You only have **1** streamer set up.",
        "If you remove it now, **Twitch Lives** will be **turned off** and you'll "
        "need to set it up again. Are you sure you want to continue?",
    ),
    (
        "notifications_youtube_video",
        "pt-br",
        "⚠️ Ei, espere! Leia abaixo primeiro",
        "Você só tem **1** canal configurado.",
        "Se você removê-lo agora, a função **YouTube Vídeos** será **desativada** e "
        "você vai precisar configurá-la de novo. Tem certeza de que quer continuar?",
    ),
    (
        "notifications_youtube_video",
        "en-us",
        "⚠️ Hey, wait! Read this first",
        "You only have **1** channel set up.",
        "If you remove it now, **YouTube Videos** will be **turned off** and you'll "
        "need to set it up again. Are you sure you want to continue?",
    ),
]


@pytest.mark.parametrize(
    "title,name",
    [
        ("▶️ YouTube Vídeos", "YouTube Vídeos"),
        ("📡 Twitch Lives", "Twitch Lives"),
        ("⏸️ Pausar", "Pausar"),
        ("☀️ Bom dia", "Bom dia"),
        ("Notificações", "Notificações"),
    ],
)
def test_a_title_loses_its_leading_emoji_whatever_block_it_is_from(title, name):
    """The YouTube feature's ▶️ sits outside the ranges the pattern knew, so the
    only-item warning named "**▶️ YouTube Vídeos**" (found in round 3)."""
    assert without_leading_emoji(title) == name


@pytest.mark.parametrize("form,locale,title,lead,closing", ONLY_ITEM_WARNINGS)
def test_removing_the_only_item_warns_with_the_item_and_disables_it(
    form, locale, title, lead, closing
):
    """N4 (round 3): Remove on the only streamer or channel asks to wait, shows
    the item's block as the panel draws it between Lucas's two sentences, and
    confirming runs the disable, naming the list it saw."""
    import importlib

    paths = importlib.import_module(f"tests.behavioral.golden.paths.{form}")
    document = {
        **paths.ENABLED,
        "notifications": {
            "style": "composition",
            "values": [paths.notification("100", "only")],
        },
    }
    context = Context(document=document)
    definition, panel = _started_in(locale, form, Manage(), context=context)
    _head, block = screen(panel).components[0].groups

    asked = decided(
        definition,
        panel.session,
        evt(ItemRemovalRequested, target="notifications$0"),
        context,
    )
    asking = screen(asked)
    assert asking.title == title
    assert asking.description == "\n\n".join((lead, "\n".join(block.lines), closing))
    assert block.lines[0].endswith("#1")
    assert [button.style for button in asking.buttons] == ["danger", "secondary"]

    confirmed = decided(definition, asked.session, evt(ev.RemovalConfirmed), context)
    [commit] = [effect for effect in confirmed.effects if isinstance(effect, Commit)]
    assert commit.kind == "disable"
    assert commit.payload == {"items": document["notifications"]["values"]}


def test_the_review_keeps_the_list_remove():
    """The item controls belong to the saved list: the setup review keeps the
    list's own Remove, never one beside each item."""
    from app.settings.form.form_state import Answer

    item = {
        "channel": Answer("100"),
        "streamer": Answer("gaules"),
        "notification_messages": Answer(["oi"]),
    }
    second = {**item, "streamer": Answer("cellbit")}
    definition, review = start(
        "notifications_twitch",
        answers={"notifications": Answer((item, second))},
    )
    session = review.session.at(definition.steps[-1].key)
    shown = decided(definition, session, evt(ev.ScreenRequested))

    actions = {
        button.action
        for group in screen(shown).components[0].groups
        for button in group.actions
    }
    assert "remove:notifications" in actions
    assert not any(action.startswith("remove_this") for action in actions)


TITLE_COUNTS = [
    (
        "notifications_twitch",
        "pt-br",
        ":busts_in_silhouette: **Notificações:** 2 de 3 configurados",
    ),
    (
        "notifications_twitch",
        "en-us",
        ":busts_in_silhouette: **Notifications:** 2 of 3 set up",
    ),
    (
        "notifications_youtube_video",
        "pt-br",
        ":busts_in_silhouette: **Notificações:** 2 de 2 configurados",
    ),
    (
        "notifications_youtube_video",
        "en-us",
        ":busts_in_silhouette: **Notifications:** 2 of 2 set up",
    ),
]


def _started_in(locale, form, mode=Setup(), answers=None, context=None):
    from dataclasses import replace

    definition = REGISTRY.get(form)
    origin = replace(ORIGIN, locale=locale)
    session = new_session(
        (definition.key, definition.version),
        mode,
        origin,
        ttl_seconds=60,
        now=NOW,
        answers=answers,
    )
    return definition, decided(definition, session, evt(ev.Started), context)


@pytest.mark.parametrize("form,locale,line", TITLE_COUNTS)
def test_the_list_title_counts_its_items_of_its_most_on_the_panel(form, locale, line):
    """A2 (round 4): the title line itself carries the items saved of the most
    the list holds, in the words the form declares, with no quote under it."""
    import importlib

    paths = importlib.import_module(f"tests.behavioral.golden.paths.{form}")
    items = [paths.notification("100", "first"), paths.notification("102", "second")]
    document = {
        **paths.ENABLED,
        "notifications": {"style": "composition", "values": items},
    }
    context = Context(document=document)
    _definition, panel = _started_in(locale, form, Manage(), context=context)

    assert screen(panel).components[0].groups[0].parts[0].lines == (line,)


@pytest.mark.parametrize("form,locale,line", TITLE_COUNTS)
def test_the_setup_summary_counts_the_list_like_the_panel(form, locale, line):
    """A2 (round 4): the setup's summary draws the same title line, no quote."""
    from app.settings.form.form_state import Answer

    item_key = "streamer" if form == "notifications_twitch" else "youtuber"
    item = {
        "channel": Answer("100"),
        item_key: Answer("first"),
        "notification_messages": Answer(["oi"]),
    }
    second = {**item, item_key: Answer("second")}
    definition, review = _started_in(
        locale, form, answers={"notifications": Answer((item, second))}
    )
    session = review.session.at(definition.steps[-1].key)
    shown = decided(definition, session, evt(ev.ScreenRequested))

    lines = [
        line
        for group in screen(shown).components[0].groups
        for part in group.parts
        for line in part.lines
    ]
    assert line in lines
    assert not any(shown.startswith(">") for shown in lines)


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
    assert [part.button for part in parts] == ["add:custom_links"], (
        "there is no item to edit yet, only one to add"
    )
    assert "Links personalizados permitidos" in parts[0].lines[0]
    assert empty_value("pt-br") in parts[0].lines[0]
    actions = [button.action for group in view.groups for button in group.actions]
    assert "remove:custom_links" not in actions, "nothing to remove"


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


BIRTHDAY_DOC = {
    "guild_id": "123456789",
    "channel": {"style": "channel", "values": "100"},
    "timezone": "America/Sao_Paulo",
    "notification_time": "08:00",
    "reminders_birthday": {
        "style": "composition",
        "values": [
            {
                "user": {"value": "555", "title": "Member", "style": "user"},
                "date": {"value": "05-12", "title": "Birthday", "style": "mm_dd"},
            },
            {
                "user": {"value": "777", "title": "Member", "style": "user"},
                "date": {"value": "06-12", "title": "Birthday", "style": "mm_dd"},
            },
        ],
    },
}
BIRTHDAY_ROWS = (
    PanelRow(key="channel", title="Canal de aniversário", value="100", style="channel"),
    PanelRow(key="reminders_birthday", title="Total de aniversários", value="2"),
)
BIRTHDAY_PAGES = (
    PanelPage("Aniversários de Maio", ("- <@555>: 12 de maio",)),
    PanelPage("Aniversários de Junho", ("- <@777>: 12 de junho",)),
)


def _birthday_panel():
    context = Context(document=BIRTHDAY_DOC)
    panel = PanelExtras(rows=BIRTHDAY_ROWS, pages=BIRTHDAY_PAGES)
    definition, decision = start(
        "reminders_birthday", Manage(), context=context, panel=panel
    )
    return definition, decision, context, panel


def _page(decision):
    return next(
        part
        for group in screen(decision).components[0].groups
        for part in group.parts
        if part.action == "add:reminders_birthday"
    )


def test_the_pages_of_the_panel_turn_with_the_arrows_and_stay_on_a_redraw():
    """bd-6: the birthdays read one month at a time; ◀ is off on the first page,
    ▶ on the last, and a screen drawn again keeps the page the admin is on."""
    definition, decision, context, panel = _birthday_panel()

    first = _page(decision)
    assert first.lines[0] == "### Aniversários de Maio"
    assert [(b.action, b.disabled) for b in first.actions] == [
        ("page:0", True),
        ("page:1", False),
    ]
    turned = decided(
        definition, decision.session, evt(PageTurned, index=1), context, panel
    )
    second = _page(turned)
    assert second.lines[0] == "### Aniversários de Junho"
    assert [b.disabled for b in second.actions] == [False, True]
    again = decided(definition, turned.session, evt(ev.ScreenRequested), context, panel)
    assert _page(again).lines[0] == "### Aniversários de Junho"


def test_a_single_page_has_no_arrows():
    context = Context(document=BIRTHDAY_DOC)
    panel = PanelExtras(rows=BIRTHDAY_ROWS, pages=BIRTHDAY_PAGES[:1])
    _definition, decision = start(
        "reminders_birthday", Manage(), context=context, panel=panel
    )
    assert not _page(decision).actions


def test_search_finds_a_member_and_shows_their_item_with_edit_and_remove():
    """bd-6: Pesquisar opens the member select; the member's birthday shows
    with Editar, Remover and Voltar, and Voltar goes back to the panel."""
    definition, decision, context, panel = _birthday_panel()
    assert "remove" not in [b.action for b in screen(decision).buttons], (
        "a searched list removes from the item the search found"
    )

    asked = decided(definition, decision.session, evt(SearchRequested), context, panel)
    assert isinstance(screen(asked).components[0], Picker)
    found = decided(
        definition, asked.session, evt(ev.MemberChosen, user_id="777"), context, panel
    )
    shown = screen(found)
    block = shown.components[0].groups[0]
    assert shown.components[0].title == "🎉 Configurações de aniversário de <@777>"
    assert [b.action for b in block.actions] == [
        "edit:reminders_birthday$1",
        "remove_this:reminders_birthday$1",
    ]
    assert any("12 de junho" in line for line in block.lines)
    assert [b.action for b in shown.buttons] == ["picker_back"]

    back = decided(definition, found.session, evt(ev.PickerClosed), context, panel)
    assert screen(back).components[0].title.startswith("🎂")


def test_the_member_a_search_found_is_removed_through_the_panels_confirmation():
    """Broke as: Remover on the member a search found asked "Remover este item?"
    on a message of its own with Manter and Remover, a second removal flow beside
    the one the panel's lists use (review K1, item 8). It now asks in place, with
    Remover and Voltar, and Voltar goes back to the member."""
    definition, decision, context, panel = _birthday_panel()
    asked = decided(definition, decision.session, evt(SearchRequested), context, panel)
    found = decided(
        definition, asked.session, evt(ev.MemberChosen, user_id="777"), context, panel
    )

    removing = decided(
        definition,
        found.session,
        evt(ItemRemovalRequested, target="reminders_birthday$1"),
        context,
        panel,
    )
    assert kinds(removing) == ["Render"]
    asking = screen(removing)
    assert asking.title == "🗑️ Remover este item?"
    assert "- <@777>" in asking.description
    assert [button.action for button in asking.buttons] == [
        "remove_items",
        "picker_back",
    ]

    back = decided(definition, removing.session, evt(ev.PickerClosed), context, panel)
    title = screen(back).components[0].title
    assert title == "🎉 Configurações de aniversário de <@777>"

    confirmed = decided(
        definition, removing.session, evt(ev.RemovalConfirmed), context, panel
    )
    [commit] = [effect for effect in confirmed.effects if isinstance(effect, Commit)]
    assert commit.kind == "remove_item" and commit.payload["indexes"] == [1]


def test_the_search_answers_as_soon_as_a_member_is_picked():
    """N2: Pesquisar asked for a member and then for Confirmar; the select now
    reports the member itself, so picking one opens that member's screen."""
    definition, decision, context, panel = _birthday_panel()

    asked = screen(
        decided(definition, decision.session, evt(SearchRequested), context, panel)
    )
    [picker] = asked.components
    assert (picker.action, picker.required) == ("member", True)
    assert [b.action for b in asked.buttons] == ["picker_back"]


def test_the_edit_picker_of_a_member_list_still_asks_for_confirm():
    definition, decision, context, panel = _birthday_panel()

    asked = screen(
        decided(
            definition,
            decision.session,
            evt(ev.EditRequested, target="reminders_birthday"),
            context,
            panel,
        )
    )
    assert asked.components[0].action == "draft"
    assert [b.action for b in asked.buttons][-1] == "picker_back"
    assert len(asked.buttons) == 2


def test_the_found_screen_draws_the_picture_and_the_lines_the_feature_gives():
    """N2 and B5 (round 3): the member's screen is titled after the member, its
    lines are the birthday and the messages the feature adds, with no member
    line the title already names, and the member's picture big under them, read
    from the server's members like every member picture (review fixes K2)."""
    from dataclasses import replace

    definition, decision, context, _panel = _birthday_panel()
    context = replace(context, member_pictures={"777": "https://cdn/777.png"})
    panel = PanelExtras(
        rows=BIRTHDAY_ROWS,
        pages=BIRTHDAY_PAGES,
        found={"777": FoundItem(("💬 **Mensagens:**",))},
    )
    asked = decided(definition, decision.session, evt(SearchRequested), context, panel)
    found = decided(
        definition, asked.session, evt(ev.MemberChosen, user_id="777"), context, panel
    )

    shown = screen(found).components[0]
    assert not shown.thumbnail, "the member's picture is below, none beside the title"
    [block] = shown.groups
    assert block.media == "https://cdn/777.png"
    assert block.lines == (
        "📅 **Dia do aniversário:** 12 de junho",
        "💬 **Mensagens:**",
    )
    assert block.heading, "a heading draws the separator under the title"


def test_a_found_item_the_feature_adds_nothing_to_draws_its_lines_alone():
    definition, decision, context, panel = _birthday_panel()
    asked = decided(definition, decision.session, evt(SearchRequested), context, panel)
    found = decided(
        definition, asked.session, evt(ev.MemberChosen, user_id="555"), context, panel
    )

    [block] = screen(found).components[0].groups
    assert not block.media and "12 de maio" in block.lines[-1]


def test_the_pages_carry_the_list_add_beside_their_title_and_not_below():
    """N2: the birthdays block is titled like the list's pages with Adicionar
    beside the title, and the panel's bottom row keeps no Adicionar."""
    definition, decision, context, panel = _birthday_panel()

    page = _page(decision)
    assert (page.label, page.emoji, page.disabled) == ("Adicionar", "➕", False)
    assert page.body == ("- <@555>: 12 de maio",)
    assert "add" not in [b.action for b in screen(decision).buttons]


def test_a_page_with_no_item_still_shows_with_its_add_and_description():
    """N2: with no birthday the block still shows, its description saying so,
    with Adicionar beside its title."""
    empty = {
        **BIRTHDAY_DOC,
        "reminders_birthday": {"style": "composition", "values": []},
    }
    context = Context(document=empty)
    panel = PanelExtras(
        rows=BIRTHDAY_ROWS, pages=(PanelPage("Aniversários", (), "Nenhum ainda."),)
    )
    _definition, decision = start(
        "reminders_birthday", Manage(), context=context, panel=panel
    )

    page = _page(decision)
    assert page.lines == ("### Aniversários", "Nenhum ainda.")
    assert (page.body, page.actions) == ((), ())
    assert "add" not in [b.action for b in screen(decision).buttons]


def test_configurar_opens_the_reminder_settings_with_one_edit():
    """bd-6: the settings that sat on top of the panel open from Configurar,
    every row under the one Edit of the card that holds them."""
    definition, decision, context, panel = _birthday_panel()
    opened = decided(
        definition,
        decision.session,
        evt(ev.EditRequested, target="group:settings"),
        context,
        panel,
    )
    view = screen(opened).components[0]
    assert view.title == "⚙️ Configuração do Lembrete"
    [block] = view.groups
    assert [part.button for part in block.parts] == ["edit:birthday_config"]
    assert any("<#100>" in line for line in block.parts[0].lines)


def test_a_list_saved_empty_draws_its_rows_without_an_edit():
    """bd-4: rows a feature builds about its list keep no Edit while the
    saved list has no item, since there is nothing it could open."""
    from app.settings.form.manager import panel_screen
    from app.settings.form.responses.summary import panel_groups, placed

    definition = REGISTRY.get("notifications_twitch")
    rows = placed(
        (
            PanelRow(key="notifications", title="Total", value="0"),
            PanelRow(key="notifications", title="Últimos", value="-"),
        ),
        definition.steps,
        "pt-br",
    )
    assert panel_groups(rows, "x", "pt-br")[0].key == "notifications"
    empty = panel_groups(rows, "x", "pt-br", frozenset({"notifications"}))
    assert empty[0].key is None

    nothing = {"guild_id": "1", "notifications": {"style": "composition", "values": []}}
    drawn = panel_screen(definition, nothing, "pt-br", PanelExtras(rows=rows))
    assert [group.key for group in drawn.components[0].groups] == [None]


def test_remove_shows_from_one_item_or_from_two_when_the_list_is_needed():
    """Decision 5 (beta.2): an optional list can lose its last item; Twitch and
    YouTube need a streamer, so their Remove waits for a second one."""
    one_link = {
        **BLOCK_LINKS_DOC,
        "custom_links": {
            "style": "composition",
            "values": BLOCK_LINKS_DOC["custom_links"]["values"][:1],
        },
    }
    _definition, links = start(
        "block_links", Manage(), context=Context(document=one_link)
    )
    actions = [
        button.action
        for group in screen(links).components[0].groups
        for part in group.parts
        for button in part.actions
    ]
    assert actions == ["remove:custom_links"]

    from tests.behavioral.golden.paths.notifications_twitch import ENABLED, notification

    one_streamer = {
        **ENABLED,
        "notifications": {
            "style": "composition",
            "values": [notification("100", "gaules")],
        },
    }
    _definition, twitch = start(
        "notifications_twitch", Manage(), context=Context(document=one_streamer)
    )
    everything = [
        button.action
        for group in screen(twitch).components[0].groups
        for button in group.actions
    ]
    assert "remove:notifications" not in everything
    assert "remove_this:notifications$0" in everything, (
        "the only streamer keeps its own Remove, which turns the feature off (N5)"
    )


@pytest.mark.parametrize("popular", [None, "", [], "youtube.com", ["youtube.com"]])
def test_an_add_button_on_the_review_always_carries_the_plus(popular):
    """Broke as: the block links review drew the popular websites' "Adicionar"
    with ✏️ while the custom links beside it drew "➕ Adicionar": the emoji came
    from the value being a list, so nothing chosen yet (or one site) read as an
    edit. An Add label carries ➕ whatever the setting holds."""
    from dataclasses import replace

    from app.settings.form.responses.summary import panel_rows
    from tests.forms.replay import REGISTRY

    definition = REGISTRY.get("block_links")
    document = {
        **BLOCK_LINKS_DOC,
        "allowed_links": {"style": "bullet", "values": popular},
    }
    rows = [
        replace(row, group_screen=False, group_footer="")
        for row in panel_rows(definition, document, "pt-br")
    ]

    parts = [
        part
        for group in panel_groups(rows, "🚦 Bloquear Links", "pt-br")
        for part in group.parts
        if part.label == "Adicionar"
    ]

    assert len(parts) == 2, [part.lines for part in parts]
    assert {part.emoji for part in parts} == {"➕"}, [
        (part.lines, part.emoji) for part in parts
    ]
