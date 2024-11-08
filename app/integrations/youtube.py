import requests

from app import logger
from app.constants import LogTypes as logconstants


class YoutubeClient:
    def __init__(self, bot):
        from app import DiscordBot

        self.bot: DiscordBot = bot
        self.webhook_url = f"{self.bot.config.WEBHOOK_URL}/youtube"
        self.youtube_api_key = self.bot.config.YOUTUBE_API_KEY

    def get_channel_id_from_username(self, username: str) -> str:
        url = f"https://www.googleapis.com/youtube/v3/channels?part=id&forHandle={username}&key={self.youtube_api_key}"
        response = requests.get(url)
        return response.json().get("items")[0].get("id") if response.json().get("items") else None

    def get_channel_info(self, channel_id: str) -> dict:
        url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet&id={channel_id}&key={self.youtube_api_key}"
        response = requests.get(url)
        return response.json().get("items")[0].get("snippet")

    def get_video_info(self, video_id: str) -> dict:
        url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet&id={video_id}&key={self.youtube_api_key}"
        response = requests.get(url)
        return response.json().get("items")[0].get("snippet")

    def subscribe_to_new_video_event(self, channel_id: str) -> None:
        url = "https://pubsubhubbub.appspot.com/subscribe"
        body = {
            "hub.callback": f"{self.webhook_url}",
            "hub.mode": "subscribe",
            "hub.topic": f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}",
            "hub.verify": "async",
        }
        response = requests.post(url, data=body)
        logger.info(f"Subscribed to new video event for channel: {channel_id}", log_type=logconstants.COMMAND_INFO_TYPE)
        logger.info(f"Response from Youtube PubSubHubbub: {response.text}", log_type=logconstants.COMMAND_INFO_TYPE)
        return response

    def unsubscribe_from_new_video_event(self, channel_id: str) -> None:
        url = "https://pubsubhubbub.appspot.com/unsubscribe"
        body = {
            "hub.callback": f"{self.webhook_url}",
            "hub.mode": "unsubscribe",
            "hub.topic": f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}",
            "hub.verify": "async",
        }
        response = requests.post(url, data=body)
        return response

