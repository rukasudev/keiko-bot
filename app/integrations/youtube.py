import time


class YoutubeClient:
    def __init__(self, bot):
        from app import DiscordBot
        self.bot: DiscordBot = bot
        self.token = None
        self.token_expiration_time = None
        self.webhook_url = f"{self.bot.config.WEBHOOK_URL}/youtube"

    def check_auth_token(func):
        def wrapper(self, *args, **kwargs):
            current_time = time.time()
            if not self.token_expiration_time or current_time >= self.token_expiration_time:
                self.authenticate()
            return func(self, *args, **kwargs)

        return wrapper

    def authenticate(self):
        pass

    def get_channel_by_username(self, username: str) -> dict:
        pass

    @check_auth_token
    def subscribe_to_new_video_event(self, user_id: str) -> None:
        pass

    @check_auth_token
    def unsubscribe_from_new_video_event(self, subscription_id: str) -> None:
        pass

    @check_auth_token
    def get_subscriptions(self) -> dict:
        pass

    @check_auth_token
    def get_subscription_by_user_id(self, user_id: str) -> dict:
        pass
