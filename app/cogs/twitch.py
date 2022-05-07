from twitch import TwitchClient
from discord.ext import commands


class TwitchChannel(object):
    """
    Holds all the bits of information we care about for a single twitch streamer
    """

    def __init__(self, channel, stream):
        self.channel = channel
        self.stream = stream

    @property
    def is_live(self):
        return self.stream is not None

    @property
    def name(self):
        return self.channel.name

    @property
    def url(self):
        return self.channel.url


class TwitchMonitor(object):
    """
    Qeurys that status of a list of twitch streamers periodically to determine
    when they start streaming
    """

    def __init__(self, twitch_client_id, usernames):
        self.client = TwitchClient(client_id=twitch_client_id)

    def translate_username(self, name):
        ret = self.client.users.translate_usernames_to_ids([name])
        if len(ret) > 0:
            return ret[0]

        return None

    def read_streamer_info(self, user):
        channel = self.client.channels.get_by_id(user.id)
        stream = self.client.streams.get_stream_by_user(user.id)
        return TwitchChannel(channel, stream)


class TwitchCogs(commands.Cog, name="Twitch"):
    def __init__(self, bot):
        self.bot = bot


def setup(bot):
    bot.add_cog(TwitchCogs(bot))
