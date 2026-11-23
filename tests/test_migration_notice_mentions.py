"""data/0008 gives every saved notice and birthday config the mention it pinged.

Guaranteed, so nobody gets a ping they did not get before: a Twitch or YouTube notice
whose messages all open with the same @everyone, @here or role mentions (sent as
content, so they pinged) gets that mention in its own field, in the shape its card
saves, and its messages lose it; a notice whose messages disagree gets "nobody" and
keeps its messages exactly as they are, counted, with its server listed so its admin can
be told; a notice with none gets "nobody"; a mention left inside a text, which the field
no longer pings (a member included), is counted; messages that are neither a text nor a
list of texts stop the run, in `check` and in `up`; a birthday config gets @everyone
from its old switch and nobody otherwise, its default message left exactly as it is
(an embed never pinged) and only counted when it writes a mention; a member's own
birthday text is never changed, only counted; a second run changes nothing; the labels
the file writes are the ones the cards show.

The offline run goes through `up` over the project's Mongo fake, with `begin` replaced
by a block that keeps the counts and the lists the way the runner's dry run prints
them. The mongo
half runs against a throwaway replica set (MONGO_TEST_URL), like the runner's.
"""

import copy
import importlib
from types import SimpleNamespace

import pytest

from app.forms.form.form_yaml import shared_vocabulary
from tests.helpers.migrations import migrate as migrated
from tests.helpers.migrations import forget_from, run_offline, throwaway_mongo
from tests.mocks.database import MockMongoClient

MIGRATION = importlib.import_module("app.migrations.data.0008_notice_mentions")


def messages(value):
    return {"value": value, "title": "Mensagens de Notificação", "style": "code-block"}


def notice(name, value, **fields):
    return {
        "channel": {"value": "100", "title": "Canal de Texto", "style": "channel"},
        "streamer": {"value": name, "title": "Streamer"},
        "notification_messages": messages(value),
        **fields,
    }


TWITCH = {
    "_id": "1001:notifications_twitch",
    "guild_id": "1001",
    "cog": "notifications_twitch",
    "enabled": True,
    "options": {
        "notifications": {
            "style": "composition",
            "values": [
                notice("gaules", ["@everyone {streamer} está ao vivo! {stream_link}"]),
                notice("cellbit", "@here corre;@here voltou"),
                notice("shroud", ["<@&111> <@&222>, live!"]),
                notice(
                    "alanzoka",
                    ["@everyone {streamer} chegou, chama o <@123456789012345678>!"],
                ),
            ],
        }
    },
    "state": {},
}
YOUTUBE = {
    "_id": "1002:notifications_youtube_video",
    "guild_id": "1002",
    "cog": "notifications_youtube_video",
    "enabled": True,
    "options": {
        "notifications": {
            "style": "composition",
            "values": [
                notice(
                    "pewdiepie",
                    ["@everyone! {youtuber} postou", "{youtuber} postou de novo"],
                ),
                notice("sondure", ["Vídeo novo, @here e <@&333>!"]),
            ],
        }
    },
    "state": {},
}
BIRTHDAYS = [
    {
        "_id": "1001:reminders_birthday",
        "guild_id": "1001",
        "cog": "reminders_birthday",
        "enabled": True,
        "options": {
            "channel": "100",
            "mention_everyone": True,
            "default_message": {"mode": "default", "title": None, "content": None},
        },
    },
    {
        "_id": "1002:reminders_birthday",
        "guild_id": "1002",
        "cog": "reminders_birthday",
        "enabled": True,
        "options": {
            "channel": "200",
            "mention_everyone": False,
            "default_message": {
                "mode": "custom",
                "title": "🎂 Dia do {user}!",
                "content": "@here Parabéns {user}!",
            },
        },
    },
]
MEMBER = {
    "_id": "1001:555",
    "guild_id": "1001",
    "user_id": "555",
    "options": {
        "reminders_birthday": {
            "date": "05-12",
            "message": {"mode": "custom", "title": "Oi", "content": "@everyone olha!"},
        }
    },
}


def production_shaped():
    client = MockMongoClient()
    client.guild.cogs.insert_many(copy.deepcopy([TWITCH, YOUTUBE, *BIRTHDAYS]))
    client.guild.members.insert_one(copy.deepcopy(MEMBER))
    client.guild.settings.insert_many([
        {"_id": "1001", "options": {"language": "pt-br"}},
        {"_id": "1002", "options": {"language": "en-us"}},
    ])
    return client


@pytest.fixture
def offline(monkeypatch):
    return run_offline(monkeypatch, MIGRATION)


def options(client, cog_id):
    return client.guild.cogs.find_one({"_id": cog_id})["options"]


def test_a_leading_mention_is_read_with_the_punctuation_that_follows_it():
    assert MIGRATION.leading_mention("@everyone! {youtuber} postou") == (
        "everyone", (), "{youtuber} postou",
    )
    assert MIGRATION.leading_mention("  @here corre") == ("here", (), "corre")
    assert MIGRATION.leading_mention("<@&111> <@&222>, live!") == (
        "roles", ("111", "222"), "live!",
    )
    assert MIGRATION.leading_mention("live! @everyone") is None
    assert MIGRATION.leading_mention("") is None


@pytest.mark.parametrize(
    "texts",
    [
        ["<@&1> a", "@here b", "c"],
        ["@everyone a", "b"],
        ["@everyone a", "@here b"],
        ["<@&1> a", "<@&2> b"],
        ["@here a", ""],
    ],
    ids=["three-ways", "one-without", "everyone-and-here", "two-roles", "a-blank-one"],
)
def test_a_notice_whose_messages_disagree_keeps_them_and_mentions_nobody(texts):
    """v0.9.0 sent one message at random and pinged what that one opened with, so
    any mention the field could hold would ping some lives that never pinged."""
    noticed, counts = MIGRATION.notice_item(notice("x", list(texts)), "pt-br")

    assert noticed["mention"]["_raw_value"] == "none"
    assert "mention_roles" not in noticed
    assert noticed["notification_messages"]["value"] == texts
    assert counts["notices_mixed"] and not counts["notices_moved"]
    assert not counts["notices_without_mention"]


def test_messages_that_open_with_the_same_roles_in_any_order_agree():
    kind, roles, rest, mixed = MIGRATION.moved_mention(
        ["<@&1> <@&2> a", "<@&2> <@&1> b"]
    )

    assert (kind, roles, rest, mixed) == ("roles", ("1", "2"), ["a", "b"], False)


def test_a_notice_keeps_the_shape_its_card_saves():
    noticed, counts = MIGRATION.notice_item(
        notice("shroud", ["<@&111> live!"]), "pt-br"
    )

    assert noticed["mention"] == {
        "value": "Alguns cargos", "title": "Mencionar", "_raw_value": "roles",
    }
    assert noticed["mention_roles"] == {
        "value": "111", "title": "Cargos que eu menciono", "style": "role",
    }
    assert noticed["notification_messages"]["value"] == ["live!"]
    assert counts["notices_moved"] and not counts["notices_mixed"]


def test_a_notice_that_already_has_its_mention_stays_as_it_is():
    saved = notice("gaules", ["@everyone a"], mention={"_raw_value": "none"})

    assert MIGRATION.notice_item(saved, "pt-br") == (saved, {})


def test_a_birthday_mentions_only_what_its_old_switch_pinged():
    """The birthday's texts sit in an embed, so a mention written there never
    pinged: it stays in the text, is counted, and pings nobody now either."""
    written = {"mode": "custom", "title": "<@&1> Oi", "content": "@everyone olha!"}

    assert MIGRATION.birthday_options(
        {"mention_everyone": False, "default_message": written}
    ) == (
        {"mention": "none", "mention_roles": []},
        {"birthday_configs": 1, "birthday_texts_with_mention_unchanged": True},
    )
    assert MIGRATION.birthday_options({"mention_everyone": True})[0] == {
        "mention": "everyone", "mention_roles": [],
    }


def test_a_mention_the_new_field_still_pings_is_not_counted_as_lost():
    assert MIGRATION.unpinged("oi @here", "everyone", ()) == []
    assert MIGRATION.unpinged("oi <@&1> <@&2>", "roles", ("1",)) == ["<@&2>"]
    assert MIGRATION.unpinged("oi @everyone", "roles", ("1",)) == ["@everyone"]


def test_a_typed_member_or_a_wider_broadcast_is_counted_as_lost():
    """A notice now pings only its field, so a member typed in its text stops pinging,
    and so does an @everyone under @here, which is written as plain text."""
    assert MIGRATION.unpinged("oi <@555> e <@!556>", "everyone", ()) == [
        "<@555>", "<@!556>",
    ]
    assert MIGRATION.unpinged("oi @everyone", "here", ()) == ["@everyone"]


def test_the_labels_the_file_writes_are_the_ones_the_cards_show():
    texts = shared_vocabulary().texts
    for language in MIGRATION.LANGUAGES:
        assert MIGRATION.LABELS[language] == {
            kind: texts[f"mention_{kind}"].get(language)
            for kind in ("none", "everyone", "here", "roles")
        }
        assert MIGRATION.TITLES[language] == {
            "mention": texts["mention"].get(language),
            "mention_roles": texts["roles_i_mention"].get(language),
        }


def test_the_dry_run_counts_what_the_move_changes(offline):
    client = production_shaped()

    MIGRATION.up(client)

    [run] = offline
    assert run.counts == {
        "notices_moved": 4,
        "notices_without_mention": 1,
        "notices_mixed": 1,
        "messages_left_empty": 0,
        "messages_mid_text_unpinged": 2,
        "birthday_configs": 2,
        "birthday_texts_with_mention_unchanged": 1,
        "birthday_member_texts_with_mention": 1,
    }
    assert run.notes == {"notices_mixed": ["1002"]}, (
        "the dry run names the servers whose mixed notice now pings nobody"
    )


def test_each_notice_and_config_gets_the_mention_it_pinged_and_no_more(offline):
    client = production_shaped()

    MIGRATION.up(client)

    twitch = options(client, TWITCH["_id"])["notifications"]["values"]
    assert [item["mention"]["_raw_value"] for item in twitch] == [
        "everyone", "here", "roles", "everyone",
    ]
    assert [item["notification_messages"]["value"] for item in twitch] == [
        ["{streamer} está ao vivo! {stream_link}"],
        "corre;voltou",
        ["live!"],
        ["{streamer} chegou, chama o <@123456789012345678>!"],
    ]
    assert twitch[2]["mention_roles"]["value"] == ["111", "222"]
    youtube = options(client, YOUTUBE["_id"])["notifications"]["values"]
    assert [item["mention"] for item in youtube] == [
        {"value": "Nobody", "title": "Mention", "_raw_value": "none"},
        {"value": "Nobody", "title": "Mention", "_raw_value": "none"},
    ]
    assert youtube[0]["notification_messages"]["value"] == [
        "@everyone! {youtuber} postou", "{youtuber} postou de novo",
    ], "a notice whose messages disagree keeps them exactly as they are"
    assert youtube[1]["notification_messages"]["value"] == [
        "Vídeo novo, @here e <@&333>!"
    ]
    first, second = (options(client, document["_id"]) for document in BIRTHDAYS)
    assert (first["mention"], first["mention_roles"]) == ("everyone", [])
    assert (second["mention"], second["mention_roles"]) == ("none", []), (
        "an @here written in an embed never pinged, so it does not start now"
    )
    assert second["default_message"] == BIRTHDAYS[1]["options"]["default_message"], (
        "the default message stays exactly as it is, its mention included"
    )
    assert second["mention_everyone"] is False, "the old switch stays a release"
    member = client.guild.members.find_one({"_id": MEMBER["_id"]})
    assert member == MEMBER, "a member's own text is never changed"


def test_a_second_run_changes_nothing(offline):
    client = production_shaped()
    MIGRATION.up(client)
    migrated = copy.deepcopy(client.guild.cogs.find({})._data)

    MIGRATION.up(client)

    assert client.guild.cogs.find({})._data == migrated
    assert {
        name: amount for name, amount in offline[1].counts.items()
        if name != "birthday_member_texts_with_mention"
    } == {}
    assert offline[1].notes == {}


UNSPLITTABLE = [{"a": 1}, ["ok", 3], 7]


def unsplittable(value):
    broken = copy.deepcopy(TWITCH)
    broken["_id"] = "1003:notifications_twitch"
    broken["guild_id"] = "1003"
    broken["options"]["notifications"]["values"] = [notice("x", value)]
    return broken


@pytest.mark.parametrize("value", UNSPLITTABLE)
def test_the_check_refuses_messages_it_cannot_split(value):
    client = production_shaped()
    client.guild.cogs.insert_one(unsplittable(value))

    assert MIGRATION.check(SimpleNamespace(client=client)) == [
        "guild.cogs 1003:notifications_twitch: notice 0 has messages that are "
        "neither absent, a text nor a list of texts"
    ]


@pytest.mark.parametrize("value", UNSPLITTABLE)
def test_up_stops_on_messages_it_cannot_split_instead_of_writing_them(
    offline, value
):
    """On the release that ships this file, data/0003 fills guild.cogs in the same
    run, after the preflight's `check` read it empty, so `up` refuses the shape too:
    the transaction aborts and the deploy stops with the old container restarted."""
    client = production_shaped()
    client.guild.cogs.insert_one(unsplittable(value))

    with pytest.raises(
        ValueError,
        match="guild.cogs 1003:notifications_twitch: notice 0 has messages that are "
        "neither absent, a text nor a list of texts",
    ):
        MIGRATION.up(client)

    assert offline == [], "the block never reached its commit"
    saved = client.guild.cogs.find_one({"_id": "1003:notifications_twitch"})
    assert saved["options"]["notifications"]["values"][0][
        "notification_messages"
    ]["value"] == value


V090_TWITCH = {
    "guild_id": "1001",
    "enabled": True,
    "notifications": {
        "style": "composition",
        "values": [
            {"streamer": {"value": "x"}},
            {
                "streamer": {"value": "gaules"},
                "notification_messages": {
                    "value": "@everyone a;@everyone b",
                    "_raw_value": "@everyone a;@everyone b",
                    "title": "Mensagens de Notificação",
                },
            },
            {"streamer": {"value": "cellbit"}, "notification_messages": "@here corre"},
        ],
    },
}


def as_copied_by_0003(document):
    """A v0.9.0 notice document as data/0003 writes it into guild.cogs."""
    copied = importlib.import_module("app.migrations.data.0003_cogs_and_keiko")
    return copied.cog_document("notifications_twitch", copy.deepcopy(document), {})


def test_a_notice_as_0003_copies_it_from_production_moves(offline):
    """Broke as (the CI mongo job of PR #67): a production notice saved without
    messages reached 0008 through 0003 with no `notification_messages`, which
    `splittable` refused, aborting the release migration. The read model
    (`item_values`) reads such a notice as one with no messages; an envelope holding
    `_raw_value` and a bare text are read the same way."""
    client = MockMongoClient()
    client.guild.cogs.insert_one(as_copied_by_0003(V090_TWITCH))
    client.guild.settings.insert_one({"_id": "1001", "options": {"language": "pt-br"}})

    assert MIGRATION.check(SimpleNamespace(client=client)) == []
    MIGRATION.up(client)

    bare, enveloped, plain = options(client, "1001:notifications_twitch")[
        "notifications"
    ]["values"]
    assert "notification_messages" not in bare, "no messages are written where none were"
    assert bare["mention"]["_raw_value"] == "none"
    assert enveloped["mention"]["_raw_value"] == "everyone"
    assert enveloped["notification_messages"]["value"] == "a;b"
    assert enveloped["notification_messages"]["_raw_value"] == "a;b"
    assert plain["mention"]["_raw_value"] == "here"
    assert plain["notification_messages"] == "corre"


def test_an_envelope_with_no_value_is_still_refused():
    client = production_shaped()
    broken = unsplittable("x")
    broken["options"]["notifications"]["values"][0]["notification_messages"] = {
        "title": "Mensagens de Notificação"
    }
    client.guild.cogs.insert_one(broken)

    assert MIGRATION.check(SimpleNamespace(client=client)) == [
        "guild.cogs 1003:notifications_twitch: notice 0 has messages that are "
        "neither absent, a text nor a list of texts"
    ]


@pytest.fixture
def server():
    client = throwaway_mongo(__name__)
    yield client
    client.close()


def migrate(client):
    return migrated(client, "data/0008")


@pytest.mark.mongo
def test_from_empty_the_file_writes_nothing(server):
    counts = migrate(server)

    assert counts == {}


@pytest.mark.mongo
def test_production_notices_get_their_mention(server):
    migrate(server)
    forget_from(server, "data/0008")
    server.guild.cogs.insert_many(copy.deepcopy([TWITCH, YOUTUBE, *BIRTHDAYS]))
    server.guild.members.insert_one(copy.deepcopy(MEMBER))
    server.guild.settings.insert_many([
        {"_id": "1001", "options": {"language": "pt-br"}},
        {"_id": "1002", "options": {"language": "en-us"}},
    ])

    counts = migrate(server)

    assert counts["notices_moved"] == 4 and counts["birthday_configs"] == 2
    twitch = server.guild.cogs.find_one({"_id": TWITCH["_id"]})
    assert twitch["options"]["notifications"]["values"][0]["mention"]["_raw_value"] == (
        "everyone"
    )
