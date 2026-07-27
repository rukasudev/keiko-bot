"""Behavioral scenarios: /moderations birthdays reminders.

The most demanding flow in the repo: Components V2 configuration cards,
pickers, modal-input with YAML validation, conditional composition,
member sub-form, and a custom persistence_callback (persist_setup_form).
Everything below runs the real YAML + engine + services + data layer;
only the Discord transport and Mongo/Redis are fakes.
"""
import pytest

from app.services.utils import ml

pytestmark = pytest.mark.behavioral

RESUME_TITLE = {"pt-br": "Tudo certo?", "en-us": "Alright?"}
YES_LABEL = {"pt-br": "Sim", "en-us": "Yes"}
LATER_LABEL = {"pt-br": "Depois", "en-us": "Later"}
DAY_FIELD = {"pt-br": "Dia", "en-us": "Day"}


async def _complete_global_card(scenario, *, timezone="America/Sao_Paulo",
                                time_label="08:00"):
    """Fill the required fields of the birthday_config card: channel,
    timezone and notification time."""
    scenario.expect_message(components_v2=True)
    await scenario.click("customize:0")            # channel section -> picker
    await scenario.select_option("general")
    await scenario.click("customize:1")            # timezone -> value picker
    await scenario.select_option(timezone)
    await scenario.click("customize:2")            # notification time -> buttons
    await scenario.click(time_label)
    await scenario.click("done")


async def _complete_member_card(scenario, locale, *, month="05", day="12"):
    await scenario.click("customize:0")            # month -> value picker
    await scenario.select_option(month)
    await scenario.click("customize:1")            # day -> modal-input
    await scenario.submit_modal({DAY_FIELD[locale]: day})
    await scenario.click("done")


async def _run_happy_path(scenario_factory, locale):
    scenario = await scenario_factory(locale=locale).start("reminders_birthday")
    await scenario.confirm()

    await _complete_global_card(scenario)

    scenario.expect_step("register_now")
    await scenario.click(YES_LABEL[locale])        # auto_confirm option

    await scenario.select_option("Tester")         # composition: member picker
    await scenario.confirm()
    await _complete_member_card(scenario, locale)

    scenario.expect_step("confirm")
    scenario.expect_message(title_contains=RESUME_TITLE[locale])
    await scenario.confirm()                       # -> _finish -> persist_setup_form
    await scenario.finish()
    return scenario


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
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    await _complete_global_card(scenario)
    await scenario.click(YES_LABEL["pt-br"])
    await scenario.select_option("Tester")
    await scenario.confirm()

    await scenario.click("customize:0")
    await scenario.select_option("02")             # February
    await scenario.click("customize:1")
    await scenario.submit_modal({"Dia": "31"})     # invalid: 31/02

    expected_error = ml("errors.invalid-date.message", locale="pt-br")
    scenario.expect_error(expected_error.split(".")[0])

    card = scenario.current_message.view
    assert card.state.get("day") in (None, ""), "invalid day must not be stored"

    await scenario.click("customize:1")            # user retries
    await scenario.submit_modal({"Dia": "28"})
    assert card.state.get("day") == "28"

    await scenario.click("done")
    scenario.expect_step("confirm")
    await scenario.confirm()
    scenario.expect_persisted(
        "reminders", "birthdays",
        {"guild_id": str(scenario.guild.id), "user_id": "555"},
        {"date": "02-28"},
    )
    await scenario.finish()


async def test_register_later_skips_composition(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    await _complete_global_card(scenario)

    scenario.expect_step("register_now")
    await scenario.click(LATER_LABEL["pt-br"])     # condition skips composition

    scenario.expect_step("confirm")
    await scenario.confirm()

    guild_id = str(scenario.guild.id)
    scenario.expect_persisted(
        "guild", "reminders_birthday", {"guild_id": guild_id},
        {"notification_time": "08:00"},
    )
    scenario.expect_not_persisted(
        "reminders", "birthdays", {"guild_id": guild_id}
    )
    await scenario.finish()


async def test_editing_card_value_before_done_keeps_last_choice(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    await _complete_global_card(scenario, timezone="America/Sao_Paulo")

    scenario.expect_step("register_now")
    await scenario.go_back()                       # back onto the card
    scenario.expect_message(components_v2=True)
    # State must survive back-navigation (hydrated from saved responses).
    card = scenario.current_message.view
    assert card.state.get("timezone") == "America/Sao_Paulo"

    await scenario.click("customize:1")            # re-open timezone picker
    await scenario.select_option("America/New_York")
    await scenario.click("done")

    scenario.expect_step("register_now")
    await scenario.click(LATER_LABEL["pt-br"])
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
