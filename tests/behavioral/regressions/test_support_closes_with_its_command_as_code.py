"""/support closes with its `/help` line as subtext, where the backticks draw code.

Broke as: /support built its embed by hand and set its closing line, "Use the `/help`
command ...", as the embed footer, which Discord draws without markdown, so the
backticks showed around `/help`. Shared behaviour: the closing line of
`response_embed` (`app/ui/embed.py`), drawn through `with_footer` as the last line of
the embed, under its fields. Consumer that exposed it: `Support.support`
(`app/commands/support/cog.py`). Guaranteed: the answer carries no embed footer, and
it ends, under its how-to field, with the closing line as `-# • ` subtext, the slash
command in backticks, in both locales.
"""

from types import SimpleNamespace

import pytest

from tests.mocks.discord import MockInteraction, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


@pytest.mark.parametrize(
    "locale,line",
    [
        (
            "en-US",
            "-# • Use the `/help` command to better understand how to use the "
            "available commands.",
        ),
        (
            "pt-BR",
            "-# • Use o comando `/ajuda` para entender melhor como usar os comandos "
            "disponíveis.",
        ),
    ],
)
async def test_support_closes_with_its_help_line_as_subtext(guild, locale, line):
    from app.commands.support.cog import Support

    interaction = MockInteraction(user=create_member(guild), guild=guild, locale=locale)
    interaction.command = SimpleNamespace(qualified_name="support")

    await Support.support.callback(Support(SimpleNamespace()), interaction)

    embed = interaction.get_last_embed()
    assert embed.footer.text is None, "an embed footer draws the backticks raw"
    assert "-# " not in embed.description, "the line stays under the how-to field"
    assert len(embed.fields) == 2
    assert (embed.fields[-1].name, embed.fields[-1].value) == ("\u200b", line)
