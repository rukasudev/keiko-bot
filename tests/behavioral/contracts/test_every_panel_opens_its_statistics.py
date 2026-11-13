"""Every manager panel opens the statistics of its feature, with its last days.

Shared behaviour: the `stats` aside `GenericCogFeature.asides` gives every feature
(`app/settings/features/feature.py`), the "📊 Estatísticas" button the manager draws
on every panel (`panel_buttons`, `app/settings/form/manager.py`), and the embed of
`send_feature_stats` (`app/services/feature_health.py`): the feature's own lines
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
from app.core import feature_health
from app.core.utils import fill, ml
from app.settings.features import feature_for, feature_keys
from app.settings.form.form_yaml import registry
from app.settings.form.manager import PanelExtras, panel_buttons
from tests.mocks.discord import create_guild

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

RECENT = "commands.commands.commons.stats.recent"
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
    Commands.WELCOME_MESSAGES_KEY: "📊 Estatísticas de Mensagens de Boas-Vindas",
    Commands.DEFAULT_ROLES_KEY: "📊 Estatísticas de Cargos Padrão",
    Commands.BLOCK_LINKS_KEY: "📊 Estatísticas de Bloquear Links",
    Commands.NOTIFICATIONS_TWITCH_KEY: "📊 Estatísticas de Twitch Lives",
    Commands.NOTIFICATIONS_YOUTUBE_VIDEO_KEY: "📊 Estatísticas de YouTube Vídeos",
    Commands.REMINDERS_BIRTHDAY_KEY: "📊 Estatísticas de Lembretes de Aniversário",
    Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY: (
        "📊 Estatísticas de Comandos do StreamElements"
    ),
}


@pytest.mark.parametrize("key", feature_keys())
async def test_every_panel_draws_the_statistics_and_its_feature_answers_them(deps, key):
    buttons = panel_buttons(registry.get(key), {}, "pt-br", PanelExtras(), True)

    embed = await _stats(key, create_guild(id=777))

    assert [button.action for button in buttons].count("aside:stats") == 1
    assert embed.title == PORTUGUESE_TITLES[key]


async def test_a_quiet_feature_says_so_under_its_own_title(deps):
    embed = await _stats(Commands.DEFAULT_ROLES_KEY, create_guild(id=777))

    assert embed.title == ml("commands.commands.commons.stats.title.default-roles", "pt-br")
    assert _paragraphs(embed) == [fill(ml(f"{RECENT}.quiet", "pt-br"), days=DAYS)]


async def test_the_deliveries_of_the_last_days_are_counted(deps, monkeypatch):
    monkeypatch.setattr(
        feature_health, "value_counts", lambda buckets, since: {"default_roles": 3}
    )

    embed = await _stats(Commands.DEFAULT_ROLES_KEY, create_guild(id=777))

    assert _paragraphs(embed) == [
        fill(ml(f"{RECENT}.delivered-many.default-roles", "pt-br"), count=3, days=DAYS)
    ]


async def test_a_missing_permission_comes_before_the_deliveries(deps, monkeypatch):
    monkeypatch.setattr(
        feature_health, "value_counts", lambda buckets, since: {"default_roles": 3}
    )
    guild = create_guild(id=777)
    guild.me.guild_permissions = discord.Permissions.all()
    guild.me.guild_permissions.update(manage_roles=False)

    embed = await _stats(Commands.DEFAULT_ROLES_KEY, guild)

    assert ml("commands.commands.setup.permissions.names.manage_roles", "pt-br") in (
        embed.description
    )
    assert "✅" not in embed.description


async def test_block_links_keep_their_figures_above_the_last_days(deps):
    embed = await _stats(Commands.BLOCK_LINKS_KEY, create_guild(id=777))

    figures, recent = _paragraphs(embed)
    assert embed.title == ml("commands.commands.commons.stats.title.block-links", "pt-br")
    assert figures == ml(
        "commands.commands.commons.block-links-manager.stats.empty", "pt-br"
    )
    assert recent == fill(ml(f"{RECENT}.quiet", "pt-br"), days=DAYS)


async def test_birthdays_keep_their_figures_above_the_last_days(deps):
    embed = await _stats(Commands.REMINDERS_BIRTHDAY_KEY, create_guild(id=777))

    figures, recent = _paragraphs(embed)
    assert embed.title == ml("commands.commands.commons.stats.title.birthdays", "pt-br")
    assert figures.startswith("🎂 **" + ml(
        "commands.commands.commons.reminders-birthdays-manager.stats.fields.total", "pt-br"
    ))
    assert recent == fill(ml(f"{RECENT}.quiet", "pt-br"), days=DAYS)


async def _stats_in(locale, key, guild):
    interaction, sent = _interaction(guild)
    interaction.locale = locale
    await feature_for(key).asides()["stats"].handler(interaction, [])
    return sent[-1]["embed"]


@pytest.mark.parametrize("key,portuguese,english", [
    (
        Commands.WELCOME_MESSAGES_KEY,
        "📊 Estatísticas de Mensagens de Boas-Vindas",
        "📊 Welcome Messages Statistics",
    ),
    (Commands.DEFAULT_ROLES_KEY, "📊 Estatísticas de Cargos Padrão", "📊 Default Roles Statistics"),
    (Commands.BLOCK_LINKS_KEY, "📊 Estatísticas de Bloquear Links", "📊 Block Links Statistics"),
    (
        Commands.NOTIFICATIONS_TWITCH_KEY,
        "📊 Estatísticas de Twitch Lives",
        "📊 Twitch Lives Statistics",
    ),
    (
        Commands.NOTIFICATIONS_YOUTUBE_VIDEO_KEY,
        "📊 Estatísticas de YouTube Vídeos",
        "📊 YouTube Videos Statistics",
    ),
    (
        Commands.REMINDERS_BIRTHDAY_KEY,
        "📊 Estatísticas de Lembretes de Aniversário",
        "📊 Birthday Reminders Statistics",
    ),
    (
        Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY,
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
    deps.mongo_client.guild[key].insert_one({"guild_id": "777", Commands.ENABLED_KEY: True})


def _refused(deps, key, days=1):
    deps.mongo_client.guild.analytics_events.insert_one({
        "event": "value.blocked_by_permission",
        "guild_id": "777",
        "feature": key,
        "ts": _days_ago(days),
    })


def _delivered(deps, key, count, event, days=1):
    moment = _days_ago(days)
    deps.mongo_client.guild.analytics_guild_month.insert_one({
        "_id": f"777|{moment:%Y-%m}|{key}|{days}",
        "guild_id": "777",
        "month": f"{moment:%Y-%m}",
        "days": {f"{moment:%d}": {f"{event}|{key}": count}},
    })


async def _last_days(key, guild=None):
    """The statistics' closing paragraph: how the feature did in its last days."""
    embed = await _stats(key, guild or create_guild(id=777))
    return _paragraphs(embed)[-1]


async def test_a_missing_permission_is_told_before_the_refusals_and_the_deliveries(deps):
    _configure(deps, Commands.BLOCK_LINKS_KEY)
    _refused(deps, Commands.BLOCK_LINKS_KEY)
    _delivered(deps, Commands.BLOCK_LINKS_KEY, 3, "feature_action_performed")
    guild = create_guild(id=777)
    guild.me.guild_permissions = discord.Permissions.all()
    guild.me.guild_permissions.update(manage_messages=False)

    line = await _last_days(Commands.BLOCK_LINKS_KEY, guild)

    assert line == "⚠️ Preciso da permissão **Gerenciar mensagens** no servidor"


async def test_the_refused_deliveries_of_the_last_days_come_before_the_ones_made(deps):
    """Only this feature's refusals of the last seven days count, in plain words."""
    _configure(deps, Commands.BLOCK_LINKS_KEY)
    for days in (1, 2, 9):
        _refused(deps, Commands.BLOCK_LINKS_KEY, days=days)
    _refused(deps, Commands.WELCOME_MESSAGES_KEY)
    _delivered(deps, Commands.BLOCK_LINKS_KEY, 3, "feature_action_performed")

    line = await _last_days(Commands.BLOCK_LINKS_KEY)

    assert line == (
        "⚠️ Nos últimos 7 dias, 2 vezes eu não consegui fazer meu trabalho por falta de"
        " permissão. Confira em **Permissões** no `/configurar`!"
    )


@pytest.mark.parametrize("key,event,count,sentence", [
    (
        Commands.BLOCK_LINKS_KEY,
        "feature_action_performed",
        3,
        "✅ Bloqueei 3 links nos últimos 7 dias!",
    ),
    (
        Commands.BLOCK_LINKS_KEY,
        "feature_action_performed",
        1,
        "✅ Bloqueei 1 link nos últimos 7 dias!",
    ),
    (
        Commands.WELCOME_MESSAGES_KEY,
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
    deps, monkeypatch, locale, verb, count
):
    lines = {}
    for key in feature_keys():
        monkeypatch.setattr(
            feature_health, "value_counts", lambda buckets, since, key=key: {key: count}
        )
        embed = await _stats_in(locale, key, create_guild(id=777))
        lines[key] = _paragraphs(embed)[-1]

    for key, line in lines.items():
        assert "commands." not in line and "$" not in line, (key, line)
        assert line.startswith("✅ ") and f" {count} " in line, (key, line)
    assert verb in lines[Commands.WELCOME_MESSAGES_KEY]


async def test_a_feature_whose_deliveries_are_older_than_seven_days_reads_quiet(deps):
    _configure(deps, Commands.BLOCK_LINKS_KEY)
    _delivered(deps, Commands.BLOCK_LINKS_KEY, 40, "feature_action_performed", days=12)
    _refused(deps, Commands.BLOCK_LINKS_KEY, days=9)

    assert await _last_days(Commands.BLOCK_LINKS_KEY) == "💤 Nenhuma novidade nos últimos 7 dias."


async def test_the_statistics_read_the_analytics_once(deps, monkeypatch):
    _configure(deps, Commands.WELCOME_MESSAGES_KEY)
    reads = []
    for name in ("analytics_events", "analytics_guild_month"):
        collection = deps.mongo_client.guild[name]
        original = collection.find
        monkeypatch.setattr(
            collection,
            "find",
            lambda *args, original=original, name=name, **kwargs: (
                reads.append(name) or original(*args, **kwargs)
            ),
        )

    await _stats(Commands.WELCOME_MESSAGES_KEY, create_guild(id=777))

    assert sorted(reads) == ["analytics_events", "analytics_guild_month"]


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
    _refused(deps, Commands.DEFAULT_ROLES_KEY)

    embed = await _stats_in(locale, Commands.DEFAULT_ROLES_KEY, create_guild(id=777))

    assert _paragraphs(embed) == [sentence]
