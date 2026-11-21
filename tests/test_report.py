"""/reportar's own pieces: the select of commands its modal offers, the title Notion
gets, the status buttons' ids and the Answer button of the author's DM."""

import pytest

from app.commands.report.service import (
    AnswerModal,
    ReportAnswerButton,
    ReportStatusButton,
    command_options,
    report_title,
)
from app.constants import Commands, DiscordLimits
from app.core.app_commands import Listed
from app.core.i18n import fact_specs
from app.core.utils import ml
from tests.behavioral.harness.copy_keys import copy_text
from tests.helpers.slash_tree import DISCORD_LOCALES, synced_tree

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("locale", list(DISCORD_LOCALES))
async def test_the_select_names_each_command_for_its_reader_and_values_it_in_english(
    locale,
):
    listed = (await synced_tree()).listed[locale]

    options = command_options(locale=locale, listed=listed)

    *commands, other = options
    assert [(option.label, option.value) for option in commands] == [
        (command.name, command.english) for command in listed
    ]
    assert (other.label, other.value) == (
        ml("report.ui.texts.modal.other_label", locale),
        "other",
    )


def test_the_select_holds_at_most_the_options_discord_takes():
    listed = [
        Listed(f"/c{n}", f"/c{n}", "", None, menu=False, configures=False)
        for n in range(DiscordLimits.SELECT_OPTIONS + 5)
    ]

    options = command_options(locale="pt-br", listed=listed)

    assert len(options) == DiscordLimits.SELECT_OPTIONS
    assert options[-1].value == "other"


def test_a_report_title_is_its_first_words_within_the_limit():
    text = "O botão Salvar não faz nada " * 10
    word = "a" * (Commands.REPORT_TITLE_MAX * 2)

    title = report_title(text=text)

    assert len(title) <= Commands.REPORT_TITLE_MAX and title.endswith("…")
    assert text.startswith(title[:-1]) and not title[:-1].endswith(" ")
    assert report_title(text="Nada salva") == "Nada salva"
    assert len(report_title(text=word)) == Commands.REPORT_TITLE_MAX


def test_the_longest_status_button_id_fits_discord():
    button = ReportStatusButton(
        status="resolved",
        page_id="f" * 32,
        user_id=2**64 - 1,
        locale="pt-br",
        ticket="KEIKO-999999",
    )

    assert len(button.custom_id) <= DiscordLimits.CUSTOM_ID


def test_every_status_draws_its_button_routes_by_its_id_and_has_its_dm():
    """The statuses Notion takes, the buttons' looks, the custom id they route by and
    the copy of the author's DM name the same statuses."""
    statuses = list(Commands.REPORT_STATUSES)

    buttons = [
        ReportStatusButton(
            status=status,
            page_id="f" * 32,
            user_id=555,
            locale="pt-br",
            ticket="KEIKO-1234",
        )
        for status in statuses
    ]

    assert list(fact_specs("report")["status"].values) == statuses
    assert all(button.template.fullmatch(button.custom_id) for button in buttons)
    looks = {(button.item.emoji, button.item.label) for button in buttons}
    assert len(looks) == len(statuses)
    other = buttons[0].custom_id.replace(statuses[0], "closed", 1)
    assert buttons[0].template.fullmatch(other) is None


def test_an_answer_button_fits_wherever_the_status_buttons_fit():
    """The Answer button carries a ticket number as long as the longest the status
    buttons carry, so a ticket that took its buttons always takes its Answer."""
    biggest = 2**64 - 1
    one_letter = ReportStatusButton(
        status="details", page_id="f" * 32, user_id=biggest, locale="pt-br", ticket="K"
    )
    ticket = "K" * (DiscordLimits.CUSTOM_ID - len(one_letter.custom_id) + 1)

    button = ReportAnswerButton(
        message_id=biggest, user_id=biggest, locale="pt-br", ticket=ticket
    )

    assert len(button.custom_id) <= DiscordLimits.CUSTOM_ID
    assert button.template.fullmatch(button.custom_id)


def test_the_answer_button_and_the_status_buttons_route_only_their_own_ids():
    answer = ReportAnswerButton(
        message_id=1001, user_id=555, locale="pt-br", ticket="KEIKO-1234"
    )
    status = ReportStatusButton(
        status="details",
        page_id="f" * 32,
        user_id=555,
        locale="pt-br",
        ticket="KEIKO-1234",
    )

    assert answer.template.fullmatch(status.custom_id) is None
    assert status.template.fullmatch(answer.custom_id) is None


@pytest.mark.parametrize("locale", ["en-us", "pt-br"])
def test_the_question_dm_names_the_button_the_author_taps(locale):
    asks = copy_text("report.ui.responses.status.overrides.1.description", locale)

    assert f"**{ml('report.ui.buttons.answer.label', locale)}**" in asks
    assert "> $question" in asks


@pytest.mark.parametrize(
    ("question", "shown"),
    [
        pytest.param("Em qual servidor?", True, id="one-short-line"),
        pytest.param("Em qual servidor?\nE qual canal?", False, id="two-lines"),
        pytest.param(
            "a" * (DiscordLimits.MODAL_INPUT_DESCRIPTION + 1), False, id="too-long"
        ),
        pytest.param("", False, id="none-found"),
    ],
)
def test_the_answer_modal_reminds_the_question_only_when_it_fits_one_line(
    question, shown
):
    button = ReportAnswerButton(
        message_id=1001, user_id=555, locale="pt-br", ticket="KEIKO-1234"
    )

    [field] = AnswerModal(button=button, question=question).children

    assert field.description == (question if shown else None)
