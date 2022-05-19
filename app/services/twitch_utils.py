import logging
import random
import utils
import time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class TwitchChecker:
    def check_streamer(self, monitor, mongo_db):
        channels = monitor.read_all_streamer_info()
        streamers = {}
        msgs = []

        for c in channels:
            if c.name in streamers:
                if c.is_live and (not streamers[c.name].is_live):
                    logger.debug("streamer %s went live" % c.name)
                    utils.format_args[utils.FMT_TOK_STREAMER_NAME] = c.name
                    utils.format_args[utils.FMT_TOK_STREAM_URL] = c.url
                    fmtstring = random.choice(
                        mongo_db.find_one("stream_startup_messages")
                    )
                    msgs.append(fmtstring.format(**utils.format_args))

            streamers[c.name] = c

        return msgs

    # def streamer_check_loop(
    #     self, bot, monitor, mongo_db, startup_message=None, channel=None
    # ):
    #     pass

    # bot.guild_available.wait()

    # if startup_message is not None:
    #     bot.send_startup_message(startup_message)

    # while True:
    #     time.sleep(int(mongo_db.find_one("poll_period_secs")) or 30)

    #     try:
    #         msgs = self.check_streamers(
    #             monitor, mongo_db.find_one("stream_startup_messages")
    #         )
    #     except:
    #         pass

    #     for msg in msgs:
    #         logger.debug("sending message to channel")
    #         bot.send_message(msg)


# async def get_channel_by_name(self, guild):
#     pass
# print("3: ", channel_name)
# for guild in self.bot.guilds:
#     print("4: ", guild)
#     for channel in guild.text_channels:
#         if channel == channel_name:
#             print("achou")


# def start_check_is_streaming_twitch():
#     pass
# host_streamer = mongo_db.streamer.find_one({"_id": 0})
# twitch_monitor = TwitchMonitor(
#     config.TWITCH_CLIENT_ID, host_streamer["name"] or settings.data["streamer"]
# )

# twitch_checker = TwitchChecker()

# host_user = twitch_monitor.translate_username(host_streamer)
# twitch_checker.check_streamer(twitch_monitor, mongo_db["twitch"])

# startup_channel = bot.get_channel_by_name(settings.data["discord_channel_name"])
# thread = threading.Thread(
#     target=twitch_checker.streamer_check_loop,
#     args=(
#         bot,
#         twitch_monitor,
#         mongo_db["twitch"],
#         settings.data["startup_message"],
#         settings.data["discord_channel_name"],
#     ),
# )

# thread.daemon = True
# thread.start()
