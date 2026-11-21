"""Shared contract: the primitives that read a copy block.

`response_embed`, `response_error_embed`, `error_message`, `card_copy`,
`announcement_embed`, `figure_lines` and the panel helpers of `GenericCogFeature`
each read one typed block of a copy file (`app/core/i18n.py`),
the overrides its facts choose in place. The blocks here come from
`tests/helpers/copy.py`; `RecordsBrowser(copy=)` keeps its own contract in
`test_records_view.py`.
"""

import discord
import pytest

from app.constants import KeikoIcons, Style
from app.core.facts import facts
from app.core.feature_health import Figure, figure_lines
from app.core.i18n import CopyError
from app.core.utils import fill, ml
from app.forms.features.feature import GenericCogFeature, OpenContext
from app.forms.form.copy import error_message
from app.forms.form.form_yaml import shared_vocabulary
from app.ui.embed import (
    announcement_embed,
    card_copy,
    response_embed,
    response_error_embed,
    standard_footer,
)
from tests.helpers.copy import SAMPLE, checked_copy  # noqa: F401

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("copy_primitives")]


@pytest.fixture
def sample(checked_copy):  # noqa: F811
    checked_copy(SAMPLE)


def _facts(**values):
    return facts("sample", **values)


def _subtext(embed: discord.Embed) -> str:
    last = embed.fields[-1].value if embed.fields else embed.description
    last = (last or "").split("\n")[-1]
    return last if last.startswith("-# ") else ""


@pytest.mark.parametrize("locale", ["en-us", "pt-br"])
def test_a_shared_response_draws_its_copy_and_no_line_where_its_footer_is_null(
    locale,
):
    embed = response_embed("commands.responses.admin_only", locale)

    assert embed.title == ml("commands.responses.admin_only.title", locale)
    assert embed.description == ml("commands.responses.admin_only.description", locale)
    assert embed.footer.text is None and not _subtext(embed)
    assert embed.thumbnail.url is None


def test_a_response_draws_the_colour_of_its_tone():
    """A block draws Keiko's blue unless it declares `tone: warning`, the yellow of
    a message that tells the admin Keiko changed what they did."""
    warning = response_embed("commands.responses.mention_removed", "pt-br")
    default = response_embed("commands.responses.admin_only", "pt-br")

    assert warning.colour.value == int(Style.WARNING_COLOR, 16) == 0xFEE75C
    assert default.colour.value == int(Style.BACKGROUND_COLOR, 16)


@pytest.mark.parametrize(
    "locale,title,message",
    [("en-us", "✅ Saved", "I saved"), ("pt-br", "✅ Salvo", "Salvei")],
)
def test_a_response_fills_its_values_and_closes_with_the_standard_line(
    sample, locale, title, message
):
    embed = response_embed("sample.ui.responses.saved", locale, facts=_facts(item="`a`"))

    assert embed.title == title
    assert embed.description.startswith(f"{message} `a`.")
    assert _subtext(embed) == f"-# {standard_footer(locale)}"
    assert embed.footer.text is None, "the line is subtext, where markdown renders"
    assert embed.color.value == int(Style.BACKGROUND_COLOR, 16)


def test_a_null_footer_draws_no_line_and_a_text_draws_itself(sample):
    quiet = response_embed("sample.ui.responses.quiet", "en-us", facts=_facts(ping=12))
    signed = response_embed("sample.ui.responses.signed", "pt-br")

    assert quiet.description == "12 ms"
    assert _subtext(signed) == "-# • Use `/amostra` de novo"


BODILESS = """\
facts:
  ticket: {type: text, description: The ticket's id.}
ui:
  responses:
    ticket:
      title: {en-us: "📝 Ticket #$ticket", pt-br: "📝 Chamado #$ticket"}
      footer: {en-us: "Ask in `/support`", pt-br: "Pergunte em `/suporte`"}
      fields:
        title:
          title: {en-us: Title, pt-br: Título}
"""


def test_a_response_that_writes_no_description_closes_its_fields_with_its_line(
    checked_copy,  # noqa: F811
):
    checked_copy(BODILESS, prefix="bodiless")

    embed = response_embed(
        "bodiless.ui.responses.ticket",
        "pt-br",
        facts=facts("bodiless", ticket="KEIKO-7"),
        fields={"title": "Broken"},
    )

    assert embed.title == "📝 Chamado #KEIKO-7"
    assert embed.description is None, "no body above the fields"
    assert [(field.name, field.value) for field in embed.fields] == [
        ("Título", "Broken"),
        (Style.BLANK_FIELD_NAME, "-# • Pergunte em `/suporte`"),
    ], "the line closes the embed, under its fields"
    assert not embed.fields[-1].inline


def test_an_override_and_the_empty_state_replace_what_the_block_writes(sample):
    blocked = response_embed(
        "sample.ui.responses.check",
        "en-us",
        facts=_facts(blocked=True, item="x", who="a"),
    )
    allowed = response_embed(
        "sample.ui.responses.check",
        "en-us",
        facts=_facts(blocked=False, item="x", who="a"),
    )
    empty = response_embed(
        "sample.ui.responses.check",
        "pt-br",
        facts=_facts(blocked=False, item="x", who="a"),
        empty=True,
    )

    assert blocked.title == "🚫 Blocked"
    assert blocked.description.startswith("I checked x."), "it replaces only its title"
    assert allowed.title == "✅ Allowed"
    assert empty.title == "✅ Permitido"
    assert empty.description.startswith("Nada para verificar.")


def test_a_fact_the_block_reads_and_the_code_does_not_pass_is_refused(sample):
    with pytest.raises(
        CopyError, match="reads global.facts.item, sample.facts.blocked"
    ):
        response_embed("sample.ui.responses.check", "en-us", facts=_facts(who="a"))


def test_a_fact_no_catalog_declares_is_refused_at_render(sample):
    with pytest.raises(CopyError, match="sample.facts.colour is no fact of sample"):
        response_embed(
            "sample.ui.responses.saved",
            "en-us",
            facts={**_facts(item="x"), "sample.facts.colour": "red"},
        )
    with pytest.raises(CopyError, match="report.facts.ticket is no fact of sample"):
        response_embed(
            "sample.ui.responses.saved",
            "en-us",
            facts={**_facts(item="x"), "report.facts.ticket": "1"},
        )


def test_a_field_the_copy_does_not_write_is_refused(sample):
    with pytest.raises(CopyError, match="fields writes no size"):
        response_embed(
            "sample.ui.responses.check",
            "en-us",
            facts=_facts(blocked=False, item="x", who="a"),
            fields={"size": 1},
        )


def test_the_fields_follow_the_copy_order_and_its_own_values(sample):
    embed = response_embed(
        "sample.ui.responses.check",
        "en-us",
        facts=_facts(blocked=False, item="x", who="Kunal"),
        fields={"author": "<@1> wrote $who", "files": None},
        image=True,
    )

    assert [(field.name, field.value) for field in embed.fields][:3] == [
        ("How", "Ask Kunal"),
        ("Files", "No files"),
        ("Author", "<@1> wrote $who"),
    ], "a value the code gives is never filled: it may hold what a member wrote"
    assert all(not field.inline for field in embed.fields)
    assert embed.thumbnail.url == KeikoIcons.IMAGE_01


def test_a_field_with_no_value_and_no_text_of_its_own_is_left_out(sample):
    embed = response_embed(
        "sample.ui.responses.check",
        "en-us",
        facts=_facts(blocked=False, item="x", who="a"),
        inline=True,
    )

    assert [field.name for field in embed.fields] == [
        "How",
        "Files",
        Style.BLANK_FIELD_NAME,
    ]
    assert all(field.inline for field in embed.fields[:-1])


def test_an_error_block_fills_its_values(sample):
    embed = response_error_embed(
        "sample.ui.errors.not_found", "pt-br", facts=_facts(item="o canal")
    )

    assert embed.title == "🚨 Não Encontrado"
    assert embed.description.startswith("Não encontrei o canal.")
    assert embed.color.value == int(Style.RED_COLOR, 16)
    assert _subtext(embed) == f"-# {standard_footer('pt-br')}"


def test_an_error_of_the_shared_file_draws_as_it_always_did():
    embed = response_error_embed("invalid_date", "en-us")

    assert embed.title == f"🚨 {ml('errors.invalid_date.title', 'en-us')}"
    assert embed.description.startswith(ml("errors.invalid_date.message", "en-us"))


def test_the_plain_error_reads_the_message_of_the_block_it_names(sample):
    assert (
        error_message("sample.ui.errors.not_found", {"item": "a"}, "en-us")
        == "I found no a."
    )
    assert error_message("selection_required", {}, "pt-br") == ml(
        "errors.selection_required.message", "pt-br"
    )


def test_the_plain_error_fills_a_shared_block_by_its_name():
    found = error_message("required_fields", {"fields": "**A**"}, "pt-br")

    assert found == fill(ml("errors.required_fields.message", "pt-br"), fields="**A**")
    assert "**A**" in found and "$fields" not in found


def test_fill_refuses_a_value_the_line_names_in_braces():
    with pytest.raises(ValueError, match=r"copy names a value as \$date"):
        fill("Saved for **{date}**", date="May 12")
    assert fill("Saved for **$date**", date="May 12") == "Saved for **May 12**"
    assert fill("{user} keeps its braces", date="x") == "{user} keeps its braces"


def test_the_standard_line_is_the_footer_every_form_step_falls_back_to():
    footer = shared_vocabulary().fallbacks.footer

    assert standard_footer("pt-br") == footer.pt_br
    assert standard_footer("en-us") == footer.en_us


class _Sample(GenericCogFeature):
    panel_asides = {"sync": "🔄"}

    def __init__(self) -> None:
        super().__init__("sample")


def _context(locale: str = "en-us") -> OpenContext:
    return OpenContext("1", "2", locale)


def test_a_panel_button_reads_the_button_block_of_its_aside(sample):
    (button,) = _Sample().extra_buttons(_context("pt-br"))

    assert (button.label, button.action, button.emoji) == (
        "Sincronizar",
        "aside:sync",
        "🔄",
    )
    assert button.description == "Sincroniza as amostras"
    assert button.style == "secondary"


def test_a_panel_button_may_read_a_shared_button_block(sample):
    button = _Sample().panel_button("preview", "👁️", "en-us", copy="buttons.preview")

    assert button.label == ml("buttons.preview.label", "en-us")
    assert button.description == ml("buttons.preview.description", "en-us")


def test_the_panel_info_comes_from_the_copy(sample):
    class Counting(_Sample):
        fills_panel_info = True

    feature = Counting()

    assert feature.panel_info(_context("pt-br")) == "", "the feature fills it"
    assert feature.panel_info_title(_context()) == ""
    assert feature.panel_info_copy("en-us", _facts(count=3, counted=True)) == (
        "About",
        "I count 3 samples.",
    )
    assert feature.panel_info_copy("pt-br", _facts(count=0, counted=False)) == (
        "Sobre",
        "Não consegui contar.",
    )


def test_a_panel_info_that_reads_facts_is_drawn_by_its_feature(sample):
    """The generic panel passes no facts: a feature whose `panel.info` reads some
    declares `fills_panel_info` and draws it, or the panel refuses to draw."""
    with pytest.raises(CopyError, match="sample.command.panel.info reads"):
        _Sample().panel_info(_context())


def test_a_card_draws_its_texts_with_the_overrides_its_facts_choose(sample):
    pending = card_copy(
        "sample.ui.cards.dashboard", "en-us", _facts(done=False, page=1, pages=2)
    )
    done = card_copy(
        "sample.ui.cards.dashboard", "pt-br", _facts(done=True, page=2, pages=2)
    )

    assert (pending.title, pending.description, pending.note) == (
        "⚙️ Samples",
        "Everything at a glance",
        "Page 1 of 2",
    )
    assert (done.description, done.note) == ("Tudo pronto!", "Página 2 de 2")
    assert pending.footer == "", "the card writes footer: null"


def test_a_feature_whose_copy_writes_no_panel_keeps_an_empty_panel():
    feature = GenericCogFeature("report")

    assert feature.panel_info(_context()) == ""
    assert feature.panel_info_title(_context()) == ""
    assert feature.extra_buttons(_context()) == ()


def test_the_figures_of_a_statistics_embed_read_their_labels(sample):
    figures = [Figure("total", "🚫", 4), Figure("recent", "📅", 1)]

    assert figure_lines("sample", figures, "pt-br") == [
        "🚫 **Total:** 4",
        "📅 **Recentes:** 1",
    ]
    assert figure_lines("sample", [], "en-us") == ["Nothing counted yet"]
    with pytest.raises(CopyError, match="stats.figures writes no top"):
        figure_lines("sample", [Figure("top", "🏆", 1)], "en-us")


def test_the_figures_need_the_stats_block_of_their_copy():
    with pytest.raises(CopyError, match="report.command.stats: no copy file writes it"):
        figure_lines("report", [], "en-us")


def test_an_announcement_written_by_the_admin_draws_as_it_always_did():
    embed = announcement_embed("Oi", "Bem-vindo", "rodapé", "https://img")

    assert (embed.title, embed.description) == ("Oi", "Bem-vindo")
    assert embed.footer.text == "• rodapé"
    assert embed.image.url == "https://img"
    assert embed.url is None and not embed.fields


def test_an_announcement_labels_its_fields_from_the_copy(sample):
    embed = announcement_embed(
        "Live",
        "Come watch",
        "Online",
        "https://live.png",
        copy="sample.ui.announcement",
        locale="pt-br",
        fields={"game": "Chess", "tags": None},
        url="https://watch",
        colour=discord.Color.purple(),
        thumbnail="https://face.png",
    )

    assert [(field.name, field.value, field.inline) for field in embed.fields] == [
        ("Jogo", "Chess", True)
    ]
    assert embed.url == "https://watch"
    assert embed.colour == discord.Color.purple()
    assert embed.thumbnail.url == "https://face.png"


def test_an_announcement_field_the_copy_does_not_label_is_refused(sample):
    with pytest.raises(CopyError, match="fields writes no score"):
        announcement_embed("x", "y", copy="sample.ui.announcement", fields={"score": 1})


def _panel_asides():
    from app.core.commands import commands

    for command in commands():
        feature = command.feature
        if isinstance(feature, GenericCogFeature):
            for aside in feature.panel_asides:
                yield command.key, aside


@pytest.mark.parametrize("key,aside", list(_panel_asides()))
def test_every_panel_aside_a_feature_declares_writes_its_button(key, aside):
    from app.core import i18n
    from app.ui.buttons import ButtonCopy

    button = i18n.block(f"{key}.ui.buttons.{aside}", ButtonCopy)

    assert button.description is not None, (
        f"write {key}.ui.buttons.{aside}.description for Help"
    )
