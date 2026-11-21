"""Behavioral scenarios: /moderações aniversários lembretes, from the setup to a
member's birthday.

Guaranteed: the setup saves its settings (picked on the card's own selects, the
time zone on the server) and no birthday; Cancel saves nothing; a member is added
from the panel on a card that picks the member, shows their picture and greets them,
refuses a day the month does not have and a member left out, keeps the member fixed
when a birthday is edited, takes the day in a two-digit modal, and saves an uploaded
picture as its permanent URL while refusing a file the announcement could not draw;
the saved documents keep the shape the reminders read, and the birthday message
arrives with a party reaction.

Everything runs the real YAML, engine, services and data layer; only the Discord
transport and Mongo/Redis are fakes.
"""
import discord
import pytest

from app.commands.birthdays import celebration as birthday_handler
from app.commands.birthdays.service import find_birthdays_by_guild, save_birthday_config
from app.core.servers import set_feature_enabled
from app.core.utils import ml
from tests.behavioral.flows import birthdays
from tests.behavioral.flows.birthdays import complete_member_card, type_day
from tests.behavioral.flows.common import UPLOADED_URL, dump_channel
from tests.behavioral.harness.copy_keys import copy_text
from tests.generators.birthdays import saved_birthday
from tests.mocks.discord import create_member

pytestmark = pytest.mark.behavioral

SETTINGS = "reminders_birthday.form.steps.birthday_config.sections"
MEMBER_CARD = (
    "reminders_birthday.form.steps.reminders_birthday.steps.birthday_member_config"
)


def _texts(scenario):
    return [
        item.content
        for item in scenario.current_message.view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    ]


def _role_selects(scenario):
    return [
        item
        for item in scenario.current_message.view.walk_children()
        if isinstance(item, discord.ui.RoleSelect)
    ]


def _drawn(scenario):
    """The card's components, in the order Discord draws them."""
    return list(scenario.current_message.view.children[0].walk_children())


def _member_section(name, locale="pt-br"):
    return copy_text(f"{MEMBER_CARD}.sections.{name}", locale)


def server_zone(scenario):
    """The time zone the server saved, where the birthdays read it."""
    settings = scenario.get_persisted("guild", "settings", {"_id": str(scenario.guild.id)})
    return settings["options"]["timezone"]


def _saved_birthday(scenario, user_id="555"):
    return scenario.get_persisted(
        "guild", "members", {"guild_id": str(scenario.guild.id), "user_id": user_id}
    )["options"]["reminders_birthday"]


async def _member_card(scenario_factory, locale="pt-br"):
    """The setup saved, then the panel's Add on the admin's own birthday."""
    setup = await birthdays.setup(scenario_factory, locale)
    return await birthdays.to_member_card_from_the_panel(scenario_factory, setup, locale)


async def _with_a_member(scenario_factory, locale):
    """The setup saved and the admin's birthday (12/05) added from the panel."""
    scenario = await birthdays.setup(scenario_factory, locale)
    panel = await birthdays.to_member_card_from_the_panel(scenario_factory, scenario, locale)
    await complete_member_card(panel, locale)
    await panel.finish()
    return scenario


async def _panel_of(scenario_factory, deps, members=("555",)):
    await birthdays.seed_config(deps, members=members)
    return await scenario_factory(
        locale="pt-br", guild=birthdays.birthday_guild()
    ).start_command(birthdays.FORM)


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_happy_path_persists_the_settings_and_the_member(
    scenario_factory, locale
):
    scenario = await _with_a_member(scenario_factory, locale)
    guild_id = str(scenario.guild.id)
    channel = scenario.guild.text_channels[0]

    scenario.expect_persisted(
        "guild", birthdays.FORM, {"guild_id": guild_id},
        {
            "channel": str(channel.id),
            "notification_time": "08:00",
            "mention": "everyone",
            "enabled": True,
        },
    )
    assert server_zone(scenario) == "America/Sao_Paulo"
    scenario.expect_persisted(
        "guild", "members",
        {"guild_id": guild_id, "user_id": "555", "options.reminders_birthday.date": "05-12"},
        {},
    )


async def test_the_setup_saves_the_settings_and_no_birthday(scenario_factory):
    scenario = await birthdays.setup(scenario_factory, "pt-br")

    guild_id = str(scenario.guild.id)
    scenario.expect_persisted(
        "guild", birthdays.FORM, {"guild_id": guild_id}, {"notification_time": "08:00"},
    )
    scenario.expect_not_persisted("guild", "members", {"guild_id": guild_id})


async def test_editing_card_value_before_done_keeps_last_choice(scenario_factory):
    """A value picked again, the same one or another, leaves the last choice on the
    card: the one the setup saves."""
    scenario = await scenario_factory(locale="pt-br").start(birthdays.FORM)
    await scenario.confirm()
    await scenario.select_option("America/Sao_Paulo", target="timezone")
    assert scenario.card_state.get("timezone") == "America/Sao_Paulo"

    await scenario.select_option("America/New_York", target="timezone")
    await scenario.select_option("general", target="channel")
    await scenario.select_option("America/New_York", target="timezone")
    assert scenario.card_state.get("timezone") == "America/New_York"
    await scenario.select_option("08:00", target="notification_time")
    await scenario.click("done")

    scenario.expect_step("confirm")
    await scenario.confirm()

    assert server_zone(scenario) == "America/New_York"
    await scenario.finish()


async def test_the_settings_are_chosen_right_on_the_card(scenario_factory):
    """N4 (beta.2 round 2): the channel, the timezone and the time are picked on
    the card's own selects. PR8: the time starts on 10:00, so a server that
    keeps it picks nothing, and the mention is a select of who I ping, whose
    role select shows only for some roles."""
    scenario = await scenario_factory(locale="pt-br").start(birthdays.FORM)
    await scenario.confirm()

    await scenario.select_option("general", target="channel")
    await scenario.select_option("America/Sao_Paulo", target="timezone")
    time_label = copy_text(f"{SETTINGS}.notification_time.label", "pt-br")
    assert any(text.endswith(f"**{time_label}**\n> 10:00") for text in _texts(scenario))
    assert not _role_selects(scenario), "no role select while I mention nobody"
    await scenario.select_option("roles", target="mention")
    assert len(_role_selects(scenario)) == 1
    member = next(role for role in scenario.guild.roles if role.name == "Member")
    await scenario.select_option("Member", target="mention_roles")
    await scenario.click("done")
    scenario.expect_step("confirm")
    await scenario.confirm()

    config = scenario.get_persisted(
        "guild", birthdays.FORM, {"guild_id": str(scenario.guild.id)}
    )
    assert (config["notification_time"], config["mention"]) == ("10:00", "roles")
    assert config["mention_roles"] == [str(member.id)]
    assert server_zone(scenario) == "America/Sao_Paulo"
    await scenario.finish()


async def test_cancel_discards_and_persists_nothing(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start(birthdays.FORM)
    await scenario.confirm()
    scenario.expect_message(components_v2=True)
    await scenario.select_option("general", target="channel")

    await scenario.cancel()
    scenario.expect_message(
        title_contains=ml("commands.responses.discard.title", locale="pt-br")
    )
    await scenario.click(ml("buttons.discard.label", locale="pt-br"))

    guild_id = str(scenario.guild.id)
    scenario.expect_not_persisted("guild", birthdays.FORM, {"guild_id": guild_id})
    scenario.expect_not_persisted("guild", "members", {"guild_id": guild_id})
    await scenario.finish()


async def test_persisted_documents_have_expected_shape(scenario_factory):
    scenario = await _with_a_member(scenario_factory, "pt-br")
    guild_id = str(scenario.guild.id)

    config = scenario.get_persisted("guild", birthdays.FORM, {"guild_id": guild_id})
    volatile = {"created_at", "updated_at", "_id"}
    assert {k for k in config if k not in volatile} >= {
        "guild_id", "channel", "mention", "mention_roles", "notification_time",
    }
    assert "timezone" not in config, "the zone is the server's, not the birthdays'"

    item = _saved_birthday(scenario)
    assert item["date"] == "05-12"
    assert item["message"]["mode"] == "default"
    assert item["image"]["mode"] == "default"
    assert item["self_edit_count"] == 0


async def test_invalid_day_shows_error_and_recovers(scenario_factory):
    scenario = await _member_card(scenario_factory)

    await scenario.select_option("02", target="month")
    await type_day(scenario, "31")

    expected_error = ml("errors.invalid_date.message", locale="pt-br")
    scenario.expect_error(expected_error.split(".")[0])
    assert scenario.card_state.get("day") in (None, ""), "invalid day must not be stored"

    await type_day(scenario, "28")
    assert scenario.card_state.get("day") == "28"

    await scenario.click("done")
    scenario.expect_persisted(
        "guild", "members",
        {"guild_id": str(scenario.guild.id), "user_id": "555", "options.reminders_birthday.date": "02-28"},
        {},
    )
    await scenario.finish()


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
    greeting = copy_text("reminders_birthday.ui.announcement.defaults.title", "pt-br")
    assert any(
        greeting.replace("{user}", "<@777>") in (getattr(item, "content", "") or "")
        for item in drawn
    ), "the message on the card greets the member just picked"

    await complete_member_card(scenario, "pt-br", month="06", day="27")
    item = _saved_birthday(scenario, "777")
    assert item["date"] == "06-27"
    assert item["message"] == {"mode": "default", "title": None, "content": None}
    assert item["image"] == {"mode": "default", "url": None}
    await scenario.finish()


async def test_a_member_card_asks_for_its_member_before_it_saves(scenario_factory, deps):
    scenario = await _panel_of(scenario_factory, deps)
    await scenario.click("add")
    await complete_member_card(scenario, "pt-br")

    scenario.expect_error(_member_section("member.label"))
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
    member_line = f"**{_member_section('member.label')}**\n> <@777>"
    line = next(
        index
        for index, item in enumerate(drawn)
        if (getattr(item, "content", None) or "").endswith(member_line)
    )
    assert isinstance(drawn[line + 1], discord.ui.MediaGallery)
    assert len(drawn) + 4 <= 40, "the card and its buttons below fit Discord"

    await type_day(scenario, "30")
    await scenario.click("done")

    assert _saved_birthday(scenario, "777")["date"] == "06-30"
    saved = await find_birthdays_by_guild(str(scenario.guild.id))
    assert sorted(saved_item["user_id"] for saved_item in saved) == ["555", "777"]
    await scenario.finish()


async def test_the_day_is_typed_in_a_modal_of_two_digits(scenario_factory, deps):
    """B1 (beta.2 round 4): the day is typed again, on "Selecionar dia" and a
    modal whose one field takes two digits, and the card shows the day typed."""
    scenario = await _panel_of(scenario_factory, deps)
    await scenario.click("add")

    day_input = "day.modal.inputs.day"
    drawn = _drawn(scenario)
    assert not any(
        isinstance(item, discord.ui.Select)
        and (item.placeholder or "").startswith(_member_section(f"{day_input}.label"))
        for item in drawn
    ), "no select of days on the card"
    assert any(
        isinstance(item, discord.ui.Button)
        and item.label == _member_section("day.button.label")
        for item in drawn
    )

    await scenario.click("customize:day")
    (field,) = scenario.pending_modal_inputs()
    assert (field.label, field.max_length, field.placeholder) == (
        _member_section(f"{day_input}.label"),
        2,
        _member_section(f"{day_input}.placeholder"),
    )

    await scenario.submit_modal({birthdays.DAY_FIELD["pt-br"]: "27"})
    assert scenario.card_state["day"] == "27"
    await scenario.finish()


async def test_custom_image_upload_persists_permanent_url(scenario_factory, deps):
    """The real FileUploadModal (ui.Label + ui.FileUpload): the attachment is read
    for real, re-uploaded to the dump channel, and the permanent URL it gets back
    lands in the card state and in the saved birthday."""
    dumped = dump_channel(deps)
    scenario = await _member_card(scenario_factory)
    await scenario.select_option("05", target="month")
    await type_day(scenario, "12")

    await scenario.click("customize:image")
    await scenario.submit_file_upload(filename="dog.png", content=b"\x89PNG-fake")

    assert dumped.sent, "attachment must be re-uploaded to the dump channel"
    assert UPLOADED_URL in scenario.card_state.values(), (
        f"card state should hold the permanent URL, got {scenario.card_state}"
    )

    await scenario.click("done")

    item = _saved_birthday(scenario)
    assert item["image"]["mode"] == "custom"
    assert item["image"]["url"] == UPLOADED_URL
    await scenario.finish()


@pytest.mark.parametrize(
    "upload",
    [
        {"size": 11 * 1024 * 1024},
        {"width": 5000, "height": 5000},
        {"filename": "page.html", "content_type": "text/html"},
    ],
    ids=["too-heavy", "too-many-pixels", "not-a-picture"],
)
async def test_a_birthday_picture_the_banner_would_refuse_is_told_and_never_kept(
    scenario_factory, deps, upload
):
    """The birthday member card checks its picture as the welcome banner does: a
    file the announcement could not draw is refused on a message of its own, is
    never re-uploaded to the dump channel, and the member keeps the default image."""
    dumped = dump_channel(deps)
    scenario = await _member_card(scenario_factory)
    await scenario.select_option("05", target="month")
    await type_day(scenario, "12")
    before = dict(scenario.card_state)

    await scenario.click("customize:image")
    await scenario.submit_file_upload(**upload)

    scenario.expect_error(ml("errors.image_not_accepted.title", "pt-br"))
    assert dumped.sent == [], "a refused file never reaches the dump channel"
    assert scenario.card_state == before

    await scenario.click("done")
    assert _saved_birthday(scenario)["image"] == {"mode": "default", "url": None}
    await scenario.finish()


async def test_the_birthday_message_arrives_with_a_party_reaction(deps, monkeypatch):
    """The birthday message itself: what the server sees on the day.

    A celebration the server can join: the message carries a reaction.
    """
    monkeypatch.setattr(birthday_handler, "bot", deps.bot)
    guild_id = str(deps.guild.id)
    channel = deps.guild.text_channels[0]
    if not deps.guild.get_member(555):
        create_member(deps.guild, id=555, name="Tester")
    await save_birthday_config(
        guild_id, channel_id=str(channel.id), mention="none", notification_time="08:00"
    )
    saved_birthday(guild_id, "555", "05-12", reminder_id="reminder-1")
    await set_feature_enabled(guild_id, birthdays.FORM, True)

    await birthday_handler.process_birthday_webhook("reminder-1", "05-12")

    sent = channel.get_last_message()
    assert sent.embeds, "the birthday message is an embed"
    assert sent.reactions == ["🎉"]
