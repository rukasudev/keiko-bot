import resource
from datetime import datetime, timezone
from typing import Any, Dict

import discord

from app import mongo_client, redis_client
from app.bot import DiscordBot
from app.constants import Commands, KeikoIcons, LogTypes, Style
from app.data import admin as configs_data
from app.services.utils import format_datetime_output, format_relative_time


async def send_log_file_from_channel_by_date(
    date: str, interaction: discord.Interaction
) -> None:
    bot = interaction.client
    channel = bot.get_channel(bot.config.ADMIN_LOGS_FILES_CHANNEL_ID)

    async for message in channel.history(limit=None):
        if date not in message.content:
            continue

        attachment = message.attachments[0].url
        return await interaction.followup.send(
            f":page_facing_up: Here is my log file for: **{date}**! {attachment}",
        )

    await interaction.followup.send(f":pensive: Log file not found for **{date}**")


def update_admin_configs(bot: DiscordBot, data: Dict[str, Any]):
    configs_data.update_admin_configs(data)
    key, value = next(iter(data.items()))
    return setattr(bot.config, key, value)


def get_admin_configs():
    return configs_data.find_admin_configs()


def get_overview_data(bot: DiscordBot) -> dict:
    uptime = datetime.now() - bot.ready_time
    formatted_uptime = format_datetime_output(uptime)
    ready_time_utc = bot.ready_time.replace(tzinfo=timezone.utc)
    last_restart = ready_time_utc.strftime("%Y-%m-%d %H:%M") + f" ({format_relative_time(ready_time_utc)})"

    latency_ms = round(bot.latency * 1000)

    status_name = bot.status.name if bot.status else "unknown"
    activity_name = bot.activity.name if bot.activity else "N/A"

    guilds_with_members = [g for g in bot.guilds if g.member_count]
    total_users = sum(g.member_count for g in guilds_with_members)
    total_channels = sum(len(g.text_channels) + len(g.voice_channels) for g in bot.guilds)

    largest_guild = max(guilds_with_members, key=lambda g: g.member_count) if guilds_with_members else None
    loaded_cogs = len(bot.extensions)

    memory_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)

    first_day_of_month = datetime.now(tz=timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    feature_group = {
        "_id": None,
    }
    for cmd in Commands.COMMANDS_LIST:
        feature_group[cmd] = {"$sum": {"$cond": [{"$eq": [f"${cmd}", True]}, 1, 0]}}

    pipeline = [{"$facet": {
        "guild_status": [
            {"$group": {"_id": "$is_bot_online", "count": {"$sum": 1}}}
        ],
        "top_owner": [
            {"$match": {"is_bot_online": True}},
            {"$group": {"_id": "$owner_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 1}
        ],
        "feature_adoption": [
            {"$match": {"is_bot_online": True}},
            {"$group": feature_group}
        ],
        "newest_guild": [
            {"$match": {"is_bot_online": True}},
            {"$sort": {"created_at": -1}},
            {"$limit": 1},
            {"$project": {"guild_id": 1, "created_at": 1}}
        ],
        "monthly_growth": [
            {"$match": {"created_at": {"$gte": first_day_of_month}}},
            {"$count": "total"}
        ]
    }}]

    facet_result = list(mongo_client.guild.moderations.aggregate(pipeline))
    facet = facet_result[0] if facet_result else {}

    # Parse guild status
    active_guilds = 0
    inactive_guilds = 0
    for item in facet.get("guild_status", []):
        if item["_id"] is True:
            active_guilds = item["count"]
        else:
            inactive_guilds = item["count"]

    # Parse top owner
    top_owner_data = facet.get("top_owner", [])
    if top_owner_data:
        top_owner_id = top_owner_data[0]["_id"]
        top_owner_count = top_owner_data[0]["count"]
    else:
        top_owner_id = None
        top_owner_count = 0

    # Parse feature adoption
    feature_adoption = {}
    feature_data = facet.get("feature_adoption", [])
    if feature_data:
        for cmd in Commands.COMMANDS_LIST:
            feature_adoption[cmd] = feature_data[0].get(cmd, 0)
    else:
        for cmd in Commands.COMMANDS_LIST:
            feature_adoption[cmd] = 0

    # Parse newest guild
    newest_guild_data = facet.get("newest_guild", [])
    if newest_guild_data:
        newest_guild_id = newest_guild_data[0].get("guild_id")
        newest_guild_created = newest_guild_data[0].get("created_at")
    else:
        newest_guild_id = None
        newest_guild_created = None

    # Parse monthly growth
    monthly_growth_data = facet.get("monthly_growth", [])
    monthly_growth = monthly_growth_data[0]["total"] if monthly_growth_data else 0

    # Resolve newest guild name
    newest_guild_name = None
    if newest_guild_id:
        guild_obj = bot.get_guild(int(newest_guild_id))
        newest_guild_name = guild_obj.name if guild_obj else newest_guild_id

    command_calls = {}
    total_calls = 0
    for key in redis_client.scan_iter(f"{LogTypes.COMMAND_CALL_TYPE}:*"):
        cmd_name = key.split(":", 1)[1]
        count = int(redis_client.get(key) or 0)
        command_calls[cmd_name] = count
        total_calls += count

    command_errors = {}
    total_errors = 0
    for key in redis_client.scan_iter(f"{LogTypes.COMMAND_ERROR_TYPE}:*"):
        cmd_name = key.split(":", 1)[1]
        count = int(redis_client.get(key) or 0)
        command_errors[cmd_name] = count
        total_errors += count

    error_rate = round(total_errors / total_calls * 100, 1) if total_calls > 0 else None

    redis_keys = redis_client.dbsize()

    twitch_subs = 0
    twitch_unique_streamers = 0
    twitch_available = True
    try:
        subs_response = bot.twitch.get_subscriptions()
        subs_data = subs_response.get("data", [])
        twitch_subs = len(subs_data)
        twitch_unique_streamers = len({s.get("condition", {}).get("broadcaster_user_id") for s in subs_data})
    except Exception:
        twitch_available = False

    return {
        "uptime": formatted_uptime,
        "last_restart": last_restart,
        "latency_ms": latency_ms,
        "status": status_name,
        "activity": activity_name,
        "total_users": total_users,
        "total_channels": total_channels,
        "largest_guild_name": largest_guild.name if largest_guild else "N/A",
        "largest_guild_members": largest_guild.member_count if largest_guild else 0,
        "loaded_cogs": loaded_cogs,
        "memory_mb": memory_mb,
        "active_guilds": active_guilds,
        "inactive_guilds": inactive_guilds,
        "top_owner_id": top_owner_id,
        "top_owner_count": top_owner_count,
        "feature_adoption": feature_adoption,
        "newest_guild_name": newest_guild_name,
        "newest_guild_created": newest_guild_created,
        "monthly_growth": monthly_growth,
        "command_calls": command_calls,
        "total_calls": total_calls,
        "command_errors": command_errors,
        "total_errors": total_errors,
        "error_rate": error_rate,
        "redis_keys": redis_keys,
        "twitch_subs": twitch_subs,
        "twitch_unique_streamers": twitch_unique_streamers,
        "twitch_available": twitch_available,
    }


def build_overview_embed(bot: DiscordBot, data: dict) -> discord.Embed:
    embed = discord.Embed(
        title=":bar_chart: Keiko Overview",
        description=f"Online for **{data['uptime']}** | Last restart: {data['last_restart']}",
        color=int(Style.BACKGROUND_COLOR, 16),
    )
    embed.set_thumbnail(url=KeikoIcons.IMAGE_02)

    # Row 1: Latency, Status, Resources
    embed.add_field(
        name=":heartpulse: Latency",
        value=f"**{data['latency_ms']}**ms",
        inline=True,
    )
    embed.add_field(
        name=":satellite: Status",
        value=f"{data['status']} — {data['activity']}",
        inline=True,
    )
    embed.add_field(
        name=":desktop: Resources",
        value=f"RAM: **{data['memory_mb']} MB**",
        inline=True,
    )

    # Row 2: Guilds, Top Owner, Newest Guild
    embed.add_field(
        name=":homes: Guilds",
        value=f"**{data['active_guilds']}** active",
        inline=True,
    )

    if data["top_owner_id"]:
        top_owner_value = f"<@{data['top_owner_id']}> (**{data['top_owner_count']}** guilds)"
    else:
        top_owner_value = "N/A"
    embed.add_field(name=":crown: Top Owner", value=top_owner_value, inline=True)

    if data["newest_guild_name"] and data["newest_guild_created"]:
        created_dt = data["newest_guild_created"]
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        newest_value = f"{data['newest_guild_name']} ({format_relative_time(created_dt)})"
    else:
        newest_value = "N/A"
    embed.add_field(name=":new: Newest Guild", value=newest_value, inline=True)

    # Row 3: Largest Guild, Growth
    embed.add_field(
        name=":trophy: Largest Guild",
        value=f"{data['largest_guild_name']} (**{data['largest_guild_members']:,}** members)",
        inline=True,
    )
    embed.add_field(
        name=":calendar: Growth",
        value=f"**{data['monthly_growth']}** guilds this month",
        inline=True,
    )

    # Feature Adoption
    adoption_lines = []
    for cmd in Commands.COMMANDS_LIST:
        count = data["feature_adoption"].get(cmd, 0)
        icon = "\u2705" if count > 0 else "\u274c"
        label = "guild" if count == 1 else "guilds"
        adoption_lines.append(f"{icon} {cmd}: **{count}** {label}")
    embed.add_field(
        name=":tools: Feature Adoption",
        value="\n".join(adoption_lines),
        inline=False,
    )

    # Command Usage
    error_rate_str = f"**{data['error_rate']}%**" if data["error_rate"] is not None else "N/A"
    usage_lines = []
    for cmd_name in sorted(data["command_calls"], key=data["command_calls"].get, reverse=True):
        count = data["command_calls"][cmd_name]
        errors = data["command_errors"].get(cmd_name, 0)
        rate = round(errors / count * 100, 1) if count > 0 else 0
        error_info = f" | **{errors}** errors ({rate}%)" if errors > 0 else ""
        usage_lines.append(f"{cmd_name}: **{count:,}** calls{error_info}")
    usage_lines.append(f"Total: **{data['total_calls']:,}** calls")
    embed.add_field(
        name=f":joystick: Command Usage (error rate: {error_rate_str})",
        value="\n".join(usage_lines),
        inline=False,
    )

    # Integrations
    if data["twitch_available"]:
        twitch_line = f"Twitch subs: **{data['twitch_subs']}** active | **{data['twitch_unique_streamers']}** unique streamers"
    else:
        twitch_line = "Twitch: **unavailable**"

    integrations_value = (
        f"{twitch_line}\n"
        f"Redis keys: **{data['redis_keys']}**\n"
        f"Total channels: **{data['total_channels']:,}**"
    )
    embed.add_field(
        name=":satellite_orbital: Integrations",
        value=integrations_value,
        inline=False,
    )

    embed.set_footer(text=f"\u2022 Keiko {bot.config.ENVIRONMENT}")

    return embed
