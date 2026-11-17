"""Every manager panel opens the statistics of its feature, with its last days.

Shared behaviour: the `stats` aside `GenericCogFeature.asides` gives every feature
(`app/forms/features/feature.py`), the "📊 Estatísticas" button the manager draws
on every panel (`panel_buttons`, `app/forms/form/manager.py`), and the embed of
`send_feature_stats` (`app/core/feature_health.py`): the feature's own lines
(`stats_lines`, which block links and birthdays fill) and the sentence
`feature_health` writes about its last `Commands.FEATURE_STATS_DAYS` days, which
left the /configurar rows in PR15 round 2. Consumers: the seven feature panels.

Guaranteed: every feature has the aside and every panel the button; the embed is
titled after the feature; the last days say a missing permission first, then the
deliveries refused, then the ones made, else a quiet line; block links and birthdays
keep their own figures above it.
"""
from types import SimpleNamespace

import discord
import pytest

from app.constants import Commands
from app.core.utils import fill, ml
from app.forms.features import feature_for, feature_keys
from app.forms.form.form_yaml import registry
from app.forms.form.manager import PanelExtras, panel_buttons
from tests.mocks.discord import create_guild
from tests.generators.moderations import seed_cog

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

RECENT = "texts.recent"
DAYS = Commands.FEATURE_STATS_DAYS


def _interaction(guild):
    sent = []

    async def send(**kwargs):
        sent.append(kwargs)

    interaction = SimpleNamespace(
        guild=guild,
        guild_id=guild.id,
        user=SimpleNamespace(id=555),
        locale=discord.Locale.brazil_portuguese,
        followup=SimpleNamespace(send=send),
    )
    return interaction, sent


async def _stats(key, guild):
    interaction, sent = _interaction(guild)
    await feature_for(key).asides()["stats"].handler(interaction, [])
    [answer] = sent
    assert answer["ephemeral"] is True
    return answer["embed"]


def _paragraphs(embed):
    """The embed's paragraphs above its footer line."""
    *paragraphs, footer = embed.description.split("\n\n")
    assert footer.startswith("-# • ")
    return paragraphs


PORTUGUESE_TITLES = {
    "welcome_messages": "📊 Estatísticas de Mensagens de Boas-Vindas",
    "default_roles": "📊 Estatísticas de Cargos Padrão",
    "block_links": "📊 Estatísticas de Bloquear Links",
    "notifications_twitch": "📊 Estatísticas de Twitch Lives",
    "notifications_youtube_video": "📊 Estatísticas de YouTube Vídeos",
    "reminders_birthday": "📊 Estatísticas de Lembretes de Aniversário",
    "stream_elements_commands": (
        "📊 Estatísticas de Comandos do StreamElements"
    ),
}


@pytest.mark.parametrize("key", feature_keys())
async def test_every_panel_draws_the_statistics_and_its_feature_answers_them(deps, key):
    buttons = panel_buttons(registry.get(key), {}, "pt-br", PanelExtras(), True)

    embed = await _stats(key, create_guild(id=777))

    assert [button.action for button in buttons].count("aside:stats") == 1
    assert embed.title == PORTUGUESE_TITLES[key]


@pytest.mark.parametrize("key", feature_keys())
@pytest.mark.parametrize("enabled", [True, False], ids=["running", "paused"])
def test_every_panel_ends_with_its_red_delete_after_help(key, enabled):
    buttons = panel_buttons(
        registry.get(key), {}, "pt-br", PanelExtras(enabled=enabled), True
    )

    actions = [button.action for button in buttons]
    assert actions[-2:] == ["aside:help", "lifecycle:disable"]
    assert buttons[-1].style == "danger"
    assert all(button.style != "danger" for button in buttons[:-1])


async def test_a_quiet_feature_says_so_under_its_own_title(deps):
    embed = await _stats("default_roles", create_guild(id=777))

    assert embed.title == ml("default_roles.command.stats.title", "pt-br")
    assert _paragraphs(embed) == [fill(ml(f"{RECENT}.quiet", "pt-br"), days=DAYS)]


async def test_the_deliveries_of_the_last_days_are_counted(deps):
    _delivered(deps, "default_roles", 3, "value_delivered")

    embed = await _stats("default_roles", create_guild(id=777))

    assert _paragraphs(embed) == [
        fill(ml("default_roles.command.stats.delivered.other", "pt-br"), count=3, days=DAYS)
    ]


async def test_a_missing_permission_comes_before_the_deliveries(deps):
    _delivered(deps, "default_roles", 3, "value_delivered")
    guild = create_guild(id=777)
    guild.me.guild_permissions = discord.Permissions.all()
    guild.me.guild_permissions.update(manage_roles=False)

    embed = await _stats("default_roles", guild)

    assert ml("texts.permission_names.manage_roles", "pt-br") in (
        embed.description
    )
    assert "✅" not in embed.description


async def test_block_links_keep_their_figures_above_the_last_days(deps):
    embed = await _stats("block_links", create_guild(id=777))

    figures, recent = _paragraphs(embed)
    assert embed.title == ml("block_links.command.stats.title", "pt-br")
    assert figures == ml(
        "block_links.command.stats.empty", "pt-br"
    )
    assert recent == fill(ml(f"{RECENT}.quiet", "pt-br"), days=DAYS)


async def test_birthdays_keep_their_figures_above_the_last_days(deps):
    embed = await _stats("reminders_birthday", create_guild(id=777))

    figures, recent = _paragraphs(embed)
    assert embed.title == ml("reminders_birthday.command.stats.title", "pt-br")
    assert figures.startswith("🎂 **" + ml(
        "reminders_birthday.command.stats.figures.total", "pt-br"
    ))
    assert recent == fill(ml(f"{RECENT}.quiet", "pt-br"), days=DAYS)


async def _stats_in(locale, key, guild):
    interaction, sent = _interaction(guild)
    interaction.locale = locale
    await feature_for(key).asides()["stats"].handler(interaction, [])
    return sent[-1]["embed"]


@pytest.mark.parametrize("key,portuguese,english", [
    (
        "welcome_messages",
        "📊 Estatísticas de Mensagens de Boas-Vindas",
        "📊 Welcome Messages Statistics",
    ),
    ("default_roles", "📊 Estatísticas de Cargos Padrão", "📊 Default Roles Statistics"),
    ("block_links", "📊 Estatísticas de Bloquear Links", "📊 Block Links Statistics"),
    (
        "notifications_twitch",
        "📊 Estatísticas de Twitch Lives",
        "📊 Twitch Lives Statistics",
    ),
    (
        "notifications_youtube_video",
        "📊 Estatísticas de YouTube Vídeos",
        "📊 YouTube Videos Statistics",
    ),
    (
        "reminders_birthday",
        "📊 Estatísticas de Lembretes de Aniversário",
        "📊 Birthday Reminders Statistics",
    ),
    (
        "stream_elements_commands",
        "📊 Estatísticas de Comandos do StreamElements",
        "📊 StreamElements Commands Statistics",
    ),
])
async def test_the_statistics_are_titled_with_the_features_official_name(
    deps, key, portuguese, english
):
    """A feature has one name everywhere an admin reads it: the statistics said
    "boas-vindas" or "avisos de live" where the panel says its official name."""
    guild = create_guild(id=777)

    titles = [
        (await _stats_in(locale, key, guild)).title
        for locale in (discord.Locale.brazil_portuguese, discord.Locale.american_english)
    ]

    assert titles == [portuguese, english]


def _days_ago(days):
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) - timedelta(days=days)


def _configure(deps, key):
    seed_cog(deps.mongo_client, key, {"guild_id": "777", Commands.ENABLED_KEY: True})


def _refused(deps, key, days=1):
    _delivered(deps, key, 1, "value_blocked_by_permission", days=days, tag="refused")


def _delivered(deps, key, count, event, days=1, tag="delivered"):
    moment = _days_ago(days)
    deps.mongo_client.analytics.counts.insert_one({
        "_id": f"777|{moment:%Y-%m}|{key}|{days}|{tag}",
        "guild_id": "777",
        "month": f"{moment:%Y-%m}",
        "days": {f"{moment:%d}": {f"{event}|{key}": count}},
    })


async def _last_days(key, guild=None):
    """The statistics' closing paragraph: how the feature did in its last days."""
    embed = await _stats(key, guild or create_guild(id=777))
    return _paragraphs(embed)[-1]


async def test_a_missing_permission_is_told_before_the_refusals_and_the_deliveries(deps):
    _configure(deps, "block_links")
    _refused(deps, "block_links")
    _delivered(deps, "block_links", 3, "link_blocked")
    guild = create_guild(id=777)
    guild.me.guild_permissions = discord.Permissions.all()
    guild.me.guild_permissions.update(manage_messages=False)

    line = await _last_days("block_links", guild)

    assert line == "⚠️ Preciso da permissão **Gerenciar mensagens** no servidor"


async def test_the_refused_deliveries_of_the_last_days_come_before_the_ones_made(deps):
    """Only this feature's refusals of the last seven days count, in plain words."""
    _configure(deps, "block_links")
    for days in (1, 2, 9):
        _refused(deps, "block_links", days=days)
    _refused(deps, "welcome_messages")
    _delivered(deps, "block_links", 3, "link_blocked")

    line = await _last_days("block_links")

    assert line == (
        "⚠️ Nos últimos 7 dias, 2 vezes eu não consegui fazer meu trabalho por falta de"
        " permissão. Confira em **Permissões** no `/configurar`!"
    )


@pytest.mark.parametrize("key,event,count,sentence", [
    (
        "block_links",
        "link_blocked",
        3,
        "✅ Bloqueei 3 links nos últimos 7 dias!",
    ),
    (
        "block_links",
        "link_blocked",
        1,
        "✅ Bloqueei 1 link nos últimos 7 dias!",
    ),
    (
        "welcome_messages",
        "value_delivered",
        1,
        "✅ Enviei 1 mensagem de boas-vindas nos últimos 7 dias!",
    ),
])
async def test_the_deliveries_of_the_last_seven_days_are_named_per_feature(
    deps, key, event, count, sentence
):
    _configure(deps, key)
    _delivered(deps, key, count, event)
    _delivered(deps, key, 40, event, days=12)

    assert await _last_days(key) == sentence


@pytest.mark.parametrize("locale,verb", [
    (discord.Locale.brazil_portuguese, "Enviei"),
    (discord.Locale.american_english, "I sent"),
])
@pytest.mark.parametrize("count", [1, 3])
async def test_every_features_deliveries_read_as_a_sentence_in_both_languages(
    deps, locale, verb, count
):
    lines = {}
    for key in feature_keys():
        _delivered(deps, key, count, "value_delivered")
        embed = await _stats_in(locale, key, create_guild(id=777))
        lines[key] = _paragraphs(embed)[-1]

    for key, line in lines.items():
        assert "commands." not in line and "$" not in line, (key, line)
        assert line.startswith("✅ ") and f" {count} " in line, (key, line)
    assert verb in lines["welcome_messages"]


async def test_a_feature_whose_deliveries_are_older_than_seven_days_reads_quiet(deps):
    _configure(deps, "block_links")
    _delivered(deps, "block_links", 40, "link_blocked", days=12)
    _refused(deps, "block_links", days=9)

    assert await _last_days("block_links") == "💤 Nenhuma novidade nos últimos 7 dias."


async def test_the_statistics_read_the_analytics_once(deps, monkeypatch):
    _configure(deps, "welcome_messages")
    reads = []
    for name in ("counts",):
        collection = deps.mongo_client.analytics[name]
        original = collection.find
        monkeypatch.setattr(
            collection,
            "find",
            lambda *args, original=original, name=name, **kwargs: (
                reads.append(name) or original(*args, **kwargs)
            ),
        )

    await _stats("welcome_messages", create_guild(id=777))

    assert sorted(reads) == ["counts"]


@pytest.mark.parametrize("locale,sentence", [
    (
        discord.Locale.brazil_portuguese,
        "⚠️ Nos últimos 7 dias, 1 vez eu não consegui fazer meu trabalho por falta de"
        " permissão. Confira em **Permissões** no `/configurar`!",
    ),
    (
        discord.Locale.american_english,
        "⚠️ In the last 7 days, I couldn't do my job once because of a missing"
        " permission. Check **Permissions** on `/setup`!",
    ),
])
async def test_one_refused_delivery_is_told_in_the_singular(deps, locale, sentence):
    _refused(deps, "default_roles")

    embed = await _stats_in(locale, "default_roles", create_guild(id=777))

    assert _paragraphs(embed) == [sentence]
