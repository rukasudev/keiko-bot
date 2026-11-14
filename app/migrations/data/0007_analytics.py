"""Copy the month buckets to analytics.counts, the profiles to analytics.lifetime and
Keiko's block links totals to analytics.keiko; a blocked link, counted before as
`feature.action_performed`, is counted as `link.blocked`."""

from app.migrations.helpers import begin, insert_missing

ADMINS_MAX = 25
RENAMED = {"setup_opened_at": "opened_at"}
CLEARED_WHEN_ON = ("disabled_at", "paused_at")
RENAMED_METRICS = {"feature_action_performed|block_links": "link_blocked|block_links"}


def check(db):
    """Refuse a bucket, a profile or a total this file cannot copy as it is."""
    client = db.client
    return problems_in(
        client.guild.analytics_guild_month.find({}),
        client.guild.analytics_guild_profile.find({}),
        client.guild.block_links_totals.find({}),
    )


def up(db):
    """Write each copy once; a document the new collection holds already is kept."""
    with begin(db) as tx:
        buckets, renamed = [], 0
        for bucket in tx.guild.analytics_guild_month.find({}):
            document, changed = counts_document(bucket)
            buckets.append(document)
            renamed += changed
        tx.count("counts", insert_missing(tx.analytics.counts, buckets))
        tx.count("metrics_renamed", renamed)

        turned_on = enabled_cogs(
            tx.guild.cogs.find({"enabled": True}, {"guild_id": 1, "cog": 1})
        )
        lifetimes, cleared = [], 0
        for profile in tx.guild.analytics_guild_profile.find({}):
            cogs_on = turned_on.get(profile["_id"], set())
            document, changed = lifetime_document(profile, cogs_on)
            lifetimes.append(document)
            cleared += changed
        tx.count("lifetimes", insert_missing(tx.analytics.lifetime, lifetimes))
        tx.count("features_on_again", cleared)

        totals = [keiko_total(total) for total in tx.guild.block_links_totals.find({})]
        tx.count("blocked_totals", insert_missing(tx.analytics.keiko, totals))


def problems_in(buckets, profiles, totals):
    """What stops the run: the ids and fields this file reads, each named."""
    problems = []

    for bucket in buckets:
        expected = f"{bucket.get('guild_id')}|{bucket.get('month')}"
        if bucket["_id"] != expected or not isinstance(bucket.get("days"), dict):
            problems.append(
                f"guild.analytics_guild_month {bucket['_id']!r}: not a "
                "{guild}|{month} bucket with its days"
            )

    for profile in profiles:
        guild_id = profile["_id"]
        if not isinstance(guild_id, str) or not guild_id.isdigit():
            problems.append(
                f"guild.analytics_guild_profile {guild_id!r}: not a guild id"
            )
        elif not isinstance(profile.get("features") or {}, dict):
            problems.append(
                f"guild.analytics_guild_profile {guild_id}: no map of features"
            )

    for total in totals:
        named = total["_id"] == "total" or (
            str(total["_id"]).startswith("site:") and total.get("host")
        )
        if not named or not isinstance(total.get("blocked"), int):
            problems.append(
                f"guild.block_links_totals {total['_id']!r}: no total and no site"
            )

    return problems


def counts_document(bucket):
    """A month bucket as a counts document, each renamed metric under its new name."""
    days, renamed = {}, 0
    for day, metrics in bucket["days"].items():
        copied = {}
        for metric, count in metrics.items():
            name = RENAMED_METRICS.get(metric, metric)
            renamed += name != metric
            copied[name] = copied.get(name, 0) + count
        days[day] = copied
    return {**bucket, "days": days}, renamed


def enabled_cogs(documents):
    """The cogs each server has on, by server id."""
    turned_on = {}
    for document in documents:
        turned_on.setdefault(str(document["guild_id"]), set()).add(document["cog"])
    return turned_on


def lifetime_document(profile, cogs_on):
    """A profile as a lifetime: the renamed fields, the admins capped and counted, and a
    feature the server has on again no longer stamped as disabled or paused."""
    document = {key: value for key, value in profile.items() if key != "admins"}
    admins = list(dict.fromkeys(str(admin) for admin in profile.get("admins") or []))
    document["admins"] = admins[:ADMINS_MAX]
    document["admins_count"] = len(document["admins"])

    features, cleared = {}, 0
    for cog, stats in (profile.get("features") or {}).items():
        renamed = {RENAMED.get(field, field): value for field, value in stats.items()}
        stamped = [field for field in CLEARED_WHEN_ON if renamed.get(field)]
        if cog in cogs_on and stamped:
            renamed.update({field: None for field in stamped})
            cleared += 1
        features[cog] = renamed
    if "features" in profile:
        document["features"] = features

    return document, cleared


def keiko_total(total):
    """Keiko's own block links total, under the id the sink counts it by."""
    if total["_id"] == "total":
        return {"_id": "blocked:total", "blocked": total["blocked"]}
    return {
        "_id": f"blocked:site:{total['host']}",
        "host": total["host"],
        "blocked": total["blocked"],
    }
