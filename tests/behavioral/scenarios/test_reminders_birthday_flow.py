"""Behavioral scenarios: /moderations birthdays reminders.

The most demanding flow in the repo: Components V2 configuration cards,
pickers, modal-input with YAML validation, a member list that the panel
fills after the setup saves (bd-3), member sub-form, and a custom
persistence_callback (persist_setup_form).
Everything below runs the real YAML + engine + services + data layer;
only the Discord transport and Mongo/Redis are fakes.
"""
import json

import discord
import pytest

from app.services.utils import ml
from app.data.birthdays import upsert_birthday_config, upsert_birthday_item
from app.services.moderations import update_moderations_by_guild
from app.webhooks import birthday_handler
from tests.behavioral.golden.paths.reminders_birthday import _type_day
from tests.mocks.discord import create_member

pytestmark = pytest.mark.behavioral

RESUME_COMMAND = {
    "pt-br": "`/moderações aniversários lembretes`",
    "en-us": "`/moderations birthdays reminders`",
}
AFTER_SAVING = {
    "pt-br": "Ao confirmar no botão abaixo, você poderá criar os lembretes de "
    "aniversário dos membros do servidor na tela seguinte. Se preferir, cada membro "
    "também pode cadastrar o próprio aniversário com `/aniversário`!",
    "en-us": "Once you confirm below, you can create the members' birthday reminders "
    "on the next screen. Or, if you prefer, each member can add their own birthday "
    "with `/birthday`!",
}


async def _complete_global_card(scenario, *, timezone="America/Sao_Paulo",
                                time_label="08:00"):
    """Fill the required fields of the birthday_config card: channel,
    timezone and notification time."""
    scenario.expect_message(components_v2=True)
    await scenario.select_option("general", target="channel")
    await scenario.select_option(timezone, target="timezone")
    await scenario.select_option(time_label, target="notification_time")
    await scenario.click("done")


async def _complete_member_card(scenario, locale, *, month="05", day="12"):
    await scenario.select_option(month, target="month")
    await _type_day(scenario, day, locale)
    await scenario.click("done")


async def _setup(scenario_factory, locale):
    scenario = await scenario_factory(locale=locale).start("reminders_birthday")
    await scenario.confirm()
    await _complete_global_card(scenario)

    scenario.expect_step("confirm")
    resume = json.dumps(scenario.expect_message(components_v2=True), ensure_ascii=False)
    assert RESUME_COMMAND[locale] in resume
    assert AFTER_SAVING[locale] in resume
    await scenario.confirm()                       # -> _finish -> persist_setup_form
    await scenario.finish()
    return scenario


async def _to_member_card_from_the_panel(scenario_factory, scenario, locale):
    panel = await scenario_factory(
        locale=locale, guild=scenario.guild, user=scenario.user
    ).start_command("reminders_birthday")
    await panel.click("add")
    await panel.select_option("Tester", target="user")
    return panel


async def _run_happy_path(scenario_factory, locale):
    scenario = await _setup(scenario_factory, locale)
    panel = await _to_member_card_from_the_panel(scenario_factory, scenario, locale)
    await _complete_member_card(panel, locale)
    await panel.finish()
    return scenario


async def test_the_member_card_offers_a_preview_of_that_member_message(
    scenario_factory,
):
    """Lucas: the birthday setup should preview the message, per member too."""
    locale = "pt-br"
    scenario = await _setup(scenario_factory, locale)
    panel = await _to_member_card_from_the_panel(scenario_factory, scenario, locale)

    panel.expect_component(label_or_action="Pré-visualizar")
    await panel.finish()


@pytest.mark.parametrize(
    "locale,greeting,after",
    [
        ("pt-br", "deste servidor!", "Depois de salvar, você cadastra"),
        ("en-us", "in this server!", "After saving, you add"),
    ],
)
async def test_the_setup_intro_says_what_comes_after_saving_in_its_own_paragraph(
    scenario_factory, locale, greeting, after
):
    """Round 7 J1: what comes after saving is a paragraph of its own."""
    scenario = await scenario_factory(locale=locale).start("reminders_birthday")

    intro = scenario.expect_message()["embed"]["description"]
    assert f"{greeting}\n\n{after}" in intro


async def test_the_setup_review_leaves_the_birthdays_for_after_saving(
    scenario_factory,
):
    """bd-3: the birthday list left the setup. The review lists no birthday,
    offers no Add, and says where they are added once the settings are saved, in
    a note that closes the summary right above its footer (round 6 G2)."""
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    await _complete_global_card(scenario)

    scenario.expect_step("confirm")
    review = json.dumps(scenario.expect_message(components_v2=True), ensure_ascii=False)
    assert AFTER_SAVING["pt-br"] in review
    assert "Lista de Aniversários" not in review
    card, *_rows = scenario.current_message.view.children
    texts = [
        item.content for item in card.children if isinstance(item, discord.ui.TextDisplay)
    ]
    assert texts[-2:] == [
        AFTER_SAVING["pt-br"],
        "-# • Use o comando `/reportar` para me contar um bug",
    ], "the note closes the summary, right above the footer"
    header = card.children[0]
    assert AFTER_SAVING["pt-br"] not in str(
        [child.content for child in header.children]
    ), "the note left the description"
    assert "Resumo dos Lembretes" not in review
    *_card, buttons = scenario.current_message.view.children
    assert [button.label for button in buttons.children] == [
        "Pré-visualizar",
        "Confirmar",
        "Cancelar",
    ], "no Add and no Edit for a list the setup does not build"
    await scenario.finish()


async def test_happy_path_ptbr_persists_config_and_member(scenario_factory):
    scenario = await _run_happy_path(scenario_factory, "pt-br")
    guild_id = str(scenario.guild.id)
    channel = scenario.guild.text_channels[0]

    config = scenario.expect_persisted(
        "guild", "reminders_birthday", {"guild_id": guild_id},
        {
            "channel_id": str(channel.id),
            "timezone": "America/Sao_Paulo",
            "notification_time": "08:00",
            "mention_everyone": False,
        },
    )
    assert config is not None

    scenario.expect_persisted(
        "reminders", "birthdays",
        {"guild_id": guild_id, "user_id": "555"},
        {"date": "05-12"},
    )
    scenario.expect_persisted(
        "guild", "moderations", {"guild_id": guild_id},
        {"reminders_birthday": True},
    )


async def test_happy_path_enus_same_flow_in_english(scenario_factory):
    scenario = await _run_happy_path(scenario_factory, "en-us")
    scenario.expect_persisted(
        "reminders", "birthdays",
        {"guild_id": str(scenario.guild.id), "user_id": "555"},
        {"date": "05-12"},
    )


async def test_invalid_day_shows_error_and_recovers(scenario_factory):
    setup = await _setup(scenario_factory, "pt-br")
    scenario = await _to_member_card_from_the_panel(scenario_factory, setup, "pt-br")

    await scenario.select_option("02", target="month")   # February
    await _type_day(scenario, "31")                      # invalid: 31/02

    expected_error = ml("errors.invalid-date.message", locale="pt-br")
    scenario.expect_error(expected_error.split(".")[0])

    assert scenario.card_state.get("day") in (None, ""), "invalid day must not be stored"

    await _type_day(scenario, "28")                      # user retries
    assert scenario.card_state.get("day") == "28"

    await scenario.click("done")
    scenario.expect_persisted(
        "reminders", "birthdays",
        {"guild_id": str(scenario.guild.id), "user_id": "555"},
        {"date": "02-28"},
    )
    await scenario.finish()


async def test_the_setup_saves_the_settings_and_no_birthday(scenario_factory):
    scenario = await _setup(scenario_factory, "pt-br")

    guild_id = str(scenario.guild.id)
    scenario.expect_persisted(
        "guild", "reminders_birthday", {"guild_id": guild_id},
        {"notification_time": "08:00"},
    )
    scenario.expect_not_persisted(
        "reminders", "birthdays", {"guild_id": guild_id}
    )


async def test_editing_card_value_before_done_keeps_last_choice(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    await scenario.select_option("America/Sao_Paulo", target="timezone")
    assert scenario.card_state.get("timezone") == "America/Sao_Paulo"

    await scenario.select_option("America/New_York", target="timezone")
    await _complete_global_card(scenario, timezone="America/New_York")

    scenario.expect_step("confirm")
    await scenario.confirm()

    scenario.expect_persisted(
        "guild", "reminders_birthday",
        {"guild_id": str(scenario.guild.id)},
        {"timezone": "America/New_York"},
    )
    await scenario.finish()


async def test_cancel_discards_and_persists_nothing(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    scenario.expect_message(components_v2=True)

    await scenario.cancel()                        # card_cancel -> confirmation
    discard_title = ml("errors.discard-settings-confirmation.title", locale="pt-br")
    scenario.expect_message(title_contains=discard_title.split(" ", 1)[-1])

    discard_label = ml("buttons.cancel.discard", locale="pt-br")
    await scenario.click(discard_label)

    guild_id = str(scenario.guild.id)
    scenario.expect_not_persisted("guild", "reminders_birthday", {"guild_id": guild_id})
    scenario.expect_not_persisted("reminders", "birthdays", {"guild_id": guild_id})
    await scenario.finish()


async def test_persisted_documents_have_expected_shape(scenario_factory):
    scenario = await _run_happy_path(scenario_factory, "pt-br")
    guild_id = str(scenario.guild.id)

    config = scenario.get_persisted("guild", "reminders_birthday", {"guild_id": guild_id})
    volatile = {"created_at", "updated_at", "_id"}
    assert {k for k in config if k not in volatile} >= {
        "guild_id", "channel_id", "mention_everyone", "timezone",
        "notification_time",
    }

    item = scenario.get_persisted(
        "reminders", "birthdays", {"guild_id": guild_id, "user_id": "555"}
    )
    assert item["date"] == "05-12"
    assert item["message"]["mode"] == "default"
    assert item["image"]["mode"] == "default"
    assert item["self_edit_count"] == 0


async def _celebrate(deps, monkeypatch, mm_dd="05-12"):
    monkeypatch.setattr(birthday_handler, "bot", deps.bot)
    guild_id = str(deps.guild.id)
    channel = deps.guild.text_channels[0]
    if not deps.guild.get_member(555):
        create_member(deps.guild, id=555, name="Tester")
    upsert_birthday_config(
        guild_id,
        str(channel.id),
        False,
        timezone="America/Sao_Paulo",
        notification_time="08:00",
    )
    upsert_birthday_item(guild_id, "555", mm_dd, reminder_id="reminder-1")
    update_moderations_by_guild(guild_id, "reminders_birthday", True)

    await birthday_handler.process_birthday_webhook("reminder-1", mm_dd)
    return channel


async def test_the_birthday_message_arrives_with_a_party_reaction(deps, monkeypatch):
    """The birthday message itself: what the server sees on the day.

    A celebration the server can join: the message carries a reaction.
    """
    channel = await _celebrate(deps, monkeypatch)

    sent = channel._sent_messages[-1]
    assert sent.embeds, "the birthday message is an embed"
    assert sent.reactions == ["🎉"]


async def test_the_settings_are_chosen_right_on_the_card(scenario_factory):
    """N4 (beta.2 round 2): the channel, the timezone and the time are picked on
    the card's own selects, and the mention on its Sim and Não buttons."""
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()

    await scenario.select_option("general", target="channel")
    await scenario.select_option("America/Sao_Paulo", target="timezone")
    await scenario.select_option("10:00", target="notification_time")
    await scenario.click("set:mention_everyone=true")
    yes, no = (
        item
        for item in scenario.current_message.view.walk_children()
        if isinstance(item, discord.ui.Button) and item.label in ("Sim", "Não")
    )
    assert (yes.style, no.style) == (
        discord.ButtonStyle.primary,
        discord.ButtonStyle.secondary,
    )
    await scenario.click("done")
    scenario.expect_step("confirm")
    await scenario.confirm()

    scenario.expect_persisted(
        "guild", "reminders_birthday", {"guild_id": str(scenario.guild.id)},
        {
            "timezone": "America/Sao_Paulo",
            "notification_time": "10:00",
            "mention_everyone": True,
        },
    )
    await scenario.finish()


def _drawn(scenario):
    """The card's components, in the order Discord draws them."""
    from tests.behavioral.harness.locators import walk_items

    return list(walk_items(scenario.current_message.view.children[0]))


async def _panel_of(scenario_factory, deps, members=("555",)):
    from tests.behavioral.golden.paths.reminders_birthday import _guild, _seed_config

    _seed_config(deps, members=members)
    return await scenario_factory(locale="pt-br", guild=_guild()).start_command(
        "reminders_birthday"
    )


async def test_the_member_is_chosen_on_the_card_with_their_picture_under_it(
    scenario_factory, deps
):
    """B3 (beta.2 round 3): Adicionar opens the member's card at once, the
    member select is a section of it, and once a member is picked their
    picture is drawn under the select; the saved birthday keeps its shape."""
    scenario = await _panel_of(scenario_factory, deps)
    await scenario.click("add")
    scenario.expect_step("birthday_member_config")

    await scenario.select_option("Amiga", target="user")

    drawn = _drawn(scenario)
    select = next(
        index for index, item in enumerate(drawn) if isinstance(item, discord.ui.UserSelect)
    )
    assert [value.id for value in drawn[select].default_values] == [777]
    assert isinstance(drawn[select + 1], discord.ui.MediaGallery), drawn
    assert drawn[select + 1].items[0].media.url == (
        "https://cdn.discordapp.com/avatars/777/fake.png"
    )
    assert scenario.card_state["user"] == "777"
    assert any(
        "Feliz Aniversário <@777>!" in (getattr(item, "content", "") or "")
        for item in drawn
    ), "the message on the card greets the member just picked"

    await _complete_member_card(scenario, "pt-br", month="06", day="27")
    item = scenario.get_persisted(
        "reminders", "birthdays", {"guild_id": str(scenario.guild.id), "user_id": "777"}
    )
    assert item["date"] == "06-27"
    assert item["message"] == {"mode": "default", "title": None, "content": None}
    assert item["image"] == {"mode": "default", "url": None}
    await scenario.finish()


async def test_a_member_card_asks_for_its_member_before_it_saves(scenario_factory, deps):
    scenario = await _panel_of(scenario_factory, deps)
    await scenario.click("add")
    await _complete_member_card(scenario, "pt-br")

    scenario.expect_error("Membro")
    scenario.expect_step("birthday_member_config")
    await scenario.finish()


async def test_editing_a_member_keeps_the_member_fixed_on_the_card(
    scenario_factory, deps
):
    """B3 (round 3): Editar of a member a search found opens the card the Add
    opens, but the member a birthday belongs to cannot change there: the
    section reads them as a line with their picture, with no member select, and
    Confirmar saves the birthday over the same member and draws the panel again."""
    scenario = await _panel_of(scenario_factory, deps, members=("555", "777"))
    await scenario.click("search")
    await scenario.select_option("Amiga")
    await scenario.click("edit:reminders_birthday$1")

    scenario.expect_step("birthday_member_config")
    assert scenario.card_state["user"] == "777"
    drawn = _drawn(scenario)
    assert not any(isinstance(item, discord.ui.UserSelect) for item in drawn)
    line = next(
        index
        for index, item in enumerate(drawn)
        if getattr(item, "content", None) == "👤 **Membro**\n> <@777>"
    )
    assert isinstance(drawn[line + 1], discord.ui.MediaGallery)
    assert len(drawn) + 4 <= 40, "the card and its buttons below fit Discord"

    await _type_day(scenario, "30")
    await scenario.click("done")

    item = scenario.get_persisted(
        "reminders", "birthdays", {"guild_id": str(scenario.guild.id), "user_id": "777"}
    )
    assert item["date"] == "06-30"
    from app.data.birthdays import find_birthday_items_by_guild

    saved = find_birthday_items_by_guild(str(scenario.guild.id))
    assert sorted(saved_item["user_id"] for saved_item in saved) == ["555", "777"]
    await scenario.finish()


async def test_the_day_is_typed_in_a_modal_of_two_digits(scenario_factory, deps):
    """B1 (beta.2 round 4): the day is typed again, on "Selecionar dia" and a
    modal whose one field takes two digits, and the card shows the day typed."""
    scenario = await _panel_of(scenario_factory, deps)
    await scenario.click("add")

    drawn = _drawn(scenario)
    assert not any(
        isinstance(item, discord.ui.Select) and (item.placeholder or "").startswith("Dia")
        for item in drawn
    ), "no select of days on the card"
    assert any(
        isinstance(item, discord.ui.Button) and item.label == "Selecionar dia"
        for item in drawn
    )

    await scenario.click("customize:2")
    (field,) = scenario._modal_inputs(scenario.store.pending_modal)
    assert (field.label, field.max_length, field.placeholder) == ("Dia", 2, "15")

    await scenario.submit_modal({"Dia": "27"})
    assert scenario.card_state["day"] == "27"
    await scenario.finish()
