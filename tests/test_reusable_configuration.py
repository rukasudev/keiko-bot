from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from app.components.buttons import AddItemButton, CancelButton
from app.components.select_views import DesignSelectView, MultiSelectView
from app.components.embed import parse_form_dict_to_embed
from app.data.birthdays import to_summary_composition
from app.constants import FormConstants
from app.services.utils import (
    format_values_by_style,
    get_styled_composition_values,
    parse_form_yaml_to_dict,
    parse_settings_with_database_values,
)
from app.views.form import Form
from app.views.options import OptionsView
from app.views.summary_card import (
    HeaderLine,
    SummaryCardConfig,
    SummaryCardHeader,
    SummaryCardPickerView,
    SummaryCardView,
    build_configuration_card_from_step,
)
from app.views.confirm_action import (
    DiscardChangesView,
    finalize_configuration_view,
    request_discard_confirmation,
)


def _birthday_member_card():
    steps = list(parse_form_yaml_to_dict("reminders_birthday"))
    composition = next(step for step in steps if step.get("action") == "composition")
    return next(
        step
        for step in composition["steps"]
        if step.get("key") == "birthday_member_config"
    )


def _birthday_global_card():
    steps = list(parse_form_yaml_to_dict("reminders_birthday"))
    return next(step for step in steps if step.get("key") == "birthday_config")


def test_shared_card_header_supports_more_than_three_text_children():
    config = SummaryCardConfig(
        header=SummaryCardHeader(
            title="Title",
            description="Description",
            lines=[
                HeaderLine("1", "First", "A"),
                HeaderLine("2", "Second", "B"),
            ],
            thumbnail_url="https://example.com/image.png",
        ),
        sections=[],
    )

    view = SummaryCardView(
        config,
        on_done=AsyncMock(),
        locale="en-us",
    )

    container = view.children[0]
    assert isinstance(container.children[0], discord.ui.Section)
    assert len(container.children[0].children) == 3
    assert isinstance(container.children[1], discord.ui.TextDisplay)


def test_birthday_member_uses_shared_configuration_card_sections():
    card = _birthday_member_card()

    assert card["action"] == FormConstants.CONFIGURATION_CARD_ACTION_KEY
    assert [section["type"] for section in card["sections"]] == [
        "value-select",
        "modal-input",
        "title-content",
        "file-upload",
    ]


def test_register_first_birthday_screen_is_compact_and_yes_is_green():
    steps = list(parse_form_yaml_to_dict("reminders_birthday"))
    step = next(item for item in steps if item.get("key") == "register_now")

    embed = parse_form_dict_to_embed(step, "pt-br")
    with patch(
        "app.views.form.parse_form_yaml_to_dict",
        return_value=[{"key": "test", "action": "form"}],
    ):
        form = Form("test", "pt-br")
    form._step = step

    assert embed.title == "🎂 Adicionar o primeiro aniversário?"
    assert embed.description == (
        "Comece a lista agora ou deixe para depois. "
        "Os membros também podem cadastrar a própria data com /aniversario."
    )
    assert embed.footer.text is None
    assert form._get_option_styles()["True"] == discord.ButtonStyle.success
    options_view = OptionsView(
        options=form._get_options(),
        callback=AsyncMock(),
        locale="pt-br",
        styled_values=True,
        unique=True,
        auto_confirm=True,
        option_styles=form._get_option_styles(),
    )
    yes_button = next(item for item in options_view.children if item.custom_id == "True")
    assert yes_button.style == discord.ButtonStyle.success


def test_global_and_member_default_messages_match_keiko_standard():
    steps = list(parse_form_yaml_to_dict("reminders_birthday"))
    global_card = next(step for step in steps if step.get("key") == "birthday_config")
    global_message = next(
        section for section in global_card["sections"] if section.get("key") == "default_message"
    )
    member_message = next(
        section for section in _birthday_member_card()["sections"] if section.get("key") == "message"
    )

    for section in (global_message, member_message):
        assert section["default"]["title"]["pt-br"] == "🎂 Feliz Aniversário {user}!"
        assert section["default"]["content"]["pt-br"] == (
            "Que seu dia seja cheio de alegria e mimos! 🎉🐾"
        )


def test_birthday_card_saves_only_formatted_date_as_visible_value():
    card = _birthday_member_card()
    with patch(
        "app.views.form.parse_form_yaml_to_dict",
        return_value=[{"key": "test", "action": "form"}],
    ):
        form = Form("test", "pt-br")

    form._step = card
    form.view = MagicMock()
    form.view.get_response.return_value = {
        "month": "01",
        "day": "24",
        "use_custom_message": "default",
        "custom_message_title": None,
        "custom_message_content": None,
        "use_custom_image": "default",
        "custom_image": None,
    }

    form._save_step_response()

    responses = {response["key"]: response for response in form.responses}
    assert responses["date"]["value"] == "01-24"
    assert responses["date"]["style"] == "mm_dd"
    assert responses["date"]["hidden"] is False
    assert responses["month"]["hidden"] is True
    assert responses["day"]["hidden"] is True

    composition = {
        key: {
            "value": response["value"],
            "title": response["title"],
            "style": response.get("style"),
            "hidden": response.get("hidden"),
        }
        for key, response in responses.items()
    }
    rendered = get_styled_composition_values(
        "Lista de Aniversários",
        [composition],
        "pt-br",
    )
    assert "Dia do Aniversário" in rendered
    assert "24 de janeiro" in rendered
    assert "Mês do aniversário" not in rendered
    assert "\n- day:" not in rendered


def test_custom_message_and_image_are_visible_as_yes_in_summary():
    card = _birthday_member_card()
    with patch(
        "app.views.form.parse_form_yaml_to_dict",
        return_value=[{"key": "test", "action": "form"}],
    ):
        form = Form("test", "pt-br")
    form._step = card
    form.view = MagicMock()
    form.view.get_response.return_value = {
        "month": "01",
        "day": "24",
        "use_custom_message": "custom",
        "custom_message_title": "Título",
        "custom_message_content": "Conteúdo",
        "use_custom_image": "custom",
        "custom_image": "https://example.com/image.png",
    }

    form._save_step_response()

    responses = {response["key"]: response for response in form.responses}
    assert responses["use_custom_message"]["hidden"] is False
    assert responses["use_custom_message"]["style"] == "boolean-mode"
    assert responses["use_custom_image"]["hidden"] is False
    assert format_values_by_style("custom", "boolean-mode", "pt-br") == "Sim"
    assert format_values_by_style("default", "boolean-mode", "pt-br") == "Não"


def test_persisted_customizations_use_boolean_summary_style():
    summary = to_summary_composition({
        "user_id": "123",
        "date": "01-24",
        "message": {"mode": "custom", "title": "Título", "content": "Conteúdo"},
        "image": {"mode": "custom", "url": "https://example.com/image.png"},
    })

    assert summary["use_custom_message"] == {
        "value": "custom",
        "title": "Message",
        "style": "boolean-mode",
        "hidden": False,
    }
    assert summary["use_custom_image"]["style"] == "boolean-mode"
    assert summary["use_custom_image"]["hidden"] is False
    assert summary["reminder_id"]["hidden"] is True
    assert summary["self_edit_count"]["hidden"] is True


def test_birthday_card_hydrates_month_and_day_from_saved_date():
    card = _birthday_member_card()
    form = SimpleNamespace(
        locale="pt-br",
        responses=[{"key": "user", "value": "123"}],
        cogs={"date": {"value": "02-29"}},
        state=SimpleNamespace(can_go_back=False),
        _go_back=None,
        _callback=AsyncMock(),
    )
    interaction = SimpleNamespace(guild=SimpleNamespace(name="Guild"))

    view = build_configuration_card_from_step(card, form, interaction)

    assert view.state["month"] == "02"
    assert view.state["day"] == "29"


async def test_month_picker_only_clears_day_when_new_date_is_invalid():
    parent = SimpleNamespace(locale="pt-br", state={"month": "01", "day": "31"})

    def update_state(**changes):
        parent.state.update(changes)

    parent.update_state = update_state
    interaction = SimpleNamespace(
        response=SimpleNamespace(edit_message=AsyncMock()),
    )
    options = [
        {"label": {"pt-br": "Fevereiro"}, "value": "02"},
        {"label": {"pt-br": "Março"}, "value": "03"},
    ]
    rules = [{"key": "day", "validation": "validate_date"}]
    picker = SummaryCardPickerView(
        parent,
        "Escolha o mês",
        "month",
        options=options,
        reset_state_keys=rules,
    )

    picker.select._values = ["02"]
    await picker._select_option(interaction)
    assert parent.state == {"month": "02", "day": None}

    parent.state = {"month": "02", "day": "28"}
    picker.select._values = ["03"]
    await picker._select_option(interaction)
    assert parent.state == {"month": "03", "day": "28"}


async def test_required_message_uses_missing_labels_from_current_card():
    card = _birthday_member_card()
    form = SimpleNamespace(
        locale="pt-br",
        responses=[{"key": "user", "value": "123"}],
        cogs={},
        state=SimpleNamespace(can_go_back=False),
        _go_back=None,
        _callback=AsyncMock(),
    )
    view = build_configuration_card_from_step(
        card,
        form,
        SimpleNamespace(guild=SimpleNamespace(name="Guild")),
    )
    interaction = SimpleNamespace(
        data={"custom_id": "card_done"},
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    await view.interaction_check(interaction)

    message = interaction.response.send_message.await_args.args[0]
    assert message == (
        "Ainda preciso que você configure: "
        "**Mês do aniversário** e **Dia do aniversário**."
    )

    view.state["month"] = "01"
    interaction.response.send_message.reset_mock()
    await view.interaction_check(interaction)
    assert interaction.response.send_message.await_args.args[0] == (
        "Ainda preciso que você configure: **Dia do aniversário**."
    )


async def test_configuration_subscreens_inherit_each_section_icon():
    form = SimpleNamespace(
        locale="pt-br",
        responses=[],
        cogs={},
        state=SimpleNamespace(can_go_back=False),
        _go_back=None,
        _callback=AsyncMock(),
    )
    source_interaction = SimpleNamespace(guild=SimpleNamespace(name="Guild"))

    global_view = build_configuration_card_from_step(
        _birthday_global_card(),
        form,
        source_interaction,
    )
    expected_picker_icons = {
        "channel": "📺",
        "timezone": "🌎",
        "notification_time": "🕒",
    }
    for section in global_view.config.sections:
        if section.key not in expected_picker_icons:
            continue
        interaction = SimpleNamespace(
            response=SimpleNamespace(edit_message=AsyncMock()),
        )
        await section.open_customize(interaction, global_view)
        picker = interaction.response.edit_message.await_args.kwargs["view"]
        assert picker.title.startswith(f"{expected_picker_icons[section.key]} ")

    member_view = build_configuration_card_from_step(
        _birthday_member_card(),
        form,
        source_interaction,
    )
    expected_modal_icons = {
        "day": "🗓️",
        "message": "💬",
        "image": "🖼️",
    }
    for section in member_view.config.sections:
        if section.key == "month":
            interaction = SimpleNamespace(
                response=SimpleNamespace(edit_message=AsyncMock()),
            )
            await section.open_customize(interaction, member_view)
            picker = interaction.response.edit_message.await_args.kwargs["view"]
            assert picker.title.startswith("📅 ")
        elif section.key in expected_modal_icons:
            interaction = SimpleNamespace(
                response=SimpleNamespace(send_modal=AsyncMock()),
            )
            await section.open_customize(interaction, member_view)
            modal = interaction.response.send_modal.await_args.args[0]
            assert modal.title.startswith(f"{expected_modal_icons[section.key]} ")


async def test_empty_birthday_composition_still_renders_add_and_accepts_first_item():
    form = Form("reminders_birthday", "pt-br")
    resume_step = next(
        step for step in form.state.steps_list if step.get("action") == "resume"
    )
    form._step = resume_step
    form.step_embed = parse_form_dict_to_embed(resume_step, "pt-br")
    form.responses = [
        {"key": "channel", "value": "123"},
        {"key": "timezone", "value": "America/Sao_Paulo"},
        {"key": "notification_time", "value": "10:00"},
        {"key": "register_now", "value": "False"},
    ]
    interaction = SimpleNamespace()

    with patch(
        "app.views.form.get_form_settings_with_database_values",
        return_value="",
    ), patch.object(
        form,
        "_transition_from_layout_view",
        new=AsyncMock(),
    ):
        await form.show_resume(interaction)

    composition = form._get_composition_response()
    assert composition["value"] == []
    assert any(isinstance(item, AddItemButton) for item in form.children)

    first_item = {"user": {"value": "123"}, "date": {"value": "01-24"}}
    form.form_view = SimpleNamespace(
        responses=[{"key": "reminders_birthday", "value": [first_item]}],
    )
    with patch.object(form, "show_resume", new=AsyncMock()) as show_resume:
        await form.add_item_callback(interaction)

    assert composition["value"] == [first_item]
    show_resume.assert_awaited_once_with(interaction)


def test_default_roles_nested_selects_are_rendered_in_manager_summary():
    steps = list(parse_form_yaml_to_dict("default_roles"))
    cogs = {
        "enabled": True,
        "default_roles_bot": {"style": "role", "values": ["101"]},
        "default_roles": {"style": "role", "values": ["202", "203"]},
    }

    settings = parse_settings_with_database_values(cogs, steps, "pt-br")

    assert settings == [
        {
            "title": "Cargos para Bots",
            "value": {"style": "role", "values": ["101"]},
            "style": "role",
        },
        {
            "title": "Cargos para Membros",
            "value": {"style": "role", "values": ["202", "203"]},
            "style": "role",
        },
    ]


def test_multi_select_hydrates_saved_roles_and_preserves_empty_selections():
    config = {
        "selects": [
            {"type": "available_roles", "key": "bots", "style": "role"},
            {"type": "available_roles", "key": "members", "style": "role"},
        ]
    }
    view = MultiSelectView(config, callback=AsyncMock(), locale="pt-br")

    assert view.set_defaults({
        "bots": {"style": "role", "values": ["101"]},
        "members": {"style": "role", "values": ["202", "203"]},
    })

    assert [default.id for default in view.selects["bots"].default_values] == [101]
    assert [default.id for default in view.selects["members"].default_values] == [202, 203]
    view.responses["bots"] = {}
    assert view.get_response()["bots"]["values"] == []


async def test_discard_confirmation_preserves_or_finalizes_source_message():
    source_view = MagicMock()
    source_view.locale = "pt-br"
    source_interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    warning_interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        delete_original_response=AsyncMock(),
    )

    await request_discard_confirmation(source_interaction, source_view)

    source_interaction.response.defer.assert_awaited_once()
    kwargs = source_interaction.followup.send.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["embed"].colour.value == 0xFF0000
    assert kwargs["embed"].title == "🚨 Cancelar Configuração?"
    assert kwargs["embed"].description == (
        "Se você cancelar agora, vou encerrar esta mensagem e tudo o que "
        "configuramos até aqui será perdido. Para configurar de novo, você "
        "precisará começar do zero. Tem certeza de que quer descartar tudo?"
    )
    warning_view = kwargs["view"]
    assert isinstance(warning_view, DiscardChangesView)
    source_interaction.edit_original_response.assert_not_awaited()

    await warning_view.keep.callback(warning_interaction)
    warning_interaction.response.defer.assert_awaited_once()
    warning_interaction.delete_original_response.assert_awaited_once()
    source_interaction.edit_original_response.assert_not_awaited()

    warning_interaction.response.defer.reset_mock()
    warning_interaction.delete_original_response.reset_mock()
    warning_view = DiscardChangesView(source_interaction, source_view, "pt-br")
    await warning_view.discard.callback(warning_interaction)
    source_interaction.edit_original_response.assert_awaited_once_with(
        view=source_view,
    )
    warning_interaction.response.defer.assert_awaited_once()
    warning_interaction.delete_original_response.assert_awaited_once()


async def test_discard_ignores_source_message_already_deleted_by_discord():
    response = MagicMock(status=404, reason="Not Found")
    source_interaction = SimpleNamespace(
        edit_original_response=AsyncMock(
            side_effect=discord.NotFound(
                response,
                {"code": 10008, "message": "Unknown Message"},
            ),
        ),
    )
    source_view = MagicMock()
    warning_interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        delete_original_response=AsyncMock(),
    )

    view = DiscardChangesView(source_interaction, source_view, "pt-br")
    await view.discard.callback(warning_interaction)

    warning_interaction.response.defer.assert_awaited_once()
    warning_interaction.delete_original_response.assert_awaited_once()


async def test_all_configuration_cancel_paths_use_discard_confirmation():
    interaction = SimpleNamespace(data={}, response=MagicMock())
    with patch(
        "app.views.confirm_action.request_discard_confirmation",
        new=AsyncMock(),
    ) as confirmation:
        classic_view = discord.ui.View()
        classic_view.locale = "pt-br"
        cancel = CancelButton(locale="pt-br")
        classic_view.add_item(cancel)
        await cancel.callback(interaction)
        confirmation.assert_awaited_once_with(interaction, classic_view)

        confirmation.reset_mock()
        card_view = SummaryCardView(
            SummaryCardConfig(
                header=SummaryCardHeader("Título", "", [], ""),
                sections=[],
            ),
            on_done=AsyncMock(),
            locale="pt-br",
        )
        interaction.data = {"custom_id": "card_cancel"}
        await card_view.interaction_check(interaction)
        confirmation.assert_awaited_once_with(interaction, card_view)

        confirmation.reset_mock()
        design_view = DesignSelectView(
            callback=AsyncMock(),
            locale="pt-br",
            designs=[],
        )
        interaction.data = {"custom_id": "cancel_design"}
        await design_view.interaction_check(interaction)
        confirmation.assert_awaited_once_with(interaction, design_view)


def test_finalizing_layout_view_removes_actions_but_preserves_content():
    design_view = DesignSelectView(
        callback=AsyncMock(),
        locale="pt-br",
        designs=[{
            "key": "one",
            "label": {"pt-br": "Design"},
            "description": {"pt-br": "Descrição"},
        }],
    )
    container = design_view.children[0]

    assert any(isinstance(child, discord.ui.ActionRow) for child in container.children)
    assert any(isinstance(child, discord.ui.TextDisplay) for child in container.children)

    finalize_configuration_view(design_view)

    assert not any(isinstance(child, discord.ui.ActionRow) for child in container.children)
    assert any(isinstance(child, discord.ui.TextDisplay) for child in container.children)
