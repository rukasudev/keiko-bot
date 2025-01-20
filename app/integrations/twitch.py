import hashlib
import hmac
import time

import requests

TWITCH_MESSAGE_ID = 'Twitch-Eventsub-Message-Id'.lower()
TWITCH_MESSAGE_TIMESTAMP = 'Twitch-Eventsub-Message-Timestamp'.lower()
TWITCH_MESSAGE_SIGNATURE = 'Twitch-Eventsub-Message-Signature'.lower()
HMAC_PREFIX = 'sha256='

MESSAGE_TYPE = 'Twitch-Eventsub-Message-Type'.lower()
MESSAGE_TYPE_CHALLENGE = 'webhook_callback_verification'
TWITCH_API_URL = 'https://api.twitch.tv/helix'

class TwitchClient:
    def __init__(self, bot):
        from app import DiscordBot

        self.bot: DiscordBot = bot
        self.token = None
        self.token_expiration_time = None
        self.webhook_url = f"{self.bot.config.WEBHOOK_URL}/twitch"

    def _get_hmac_message(self, request: requests.Request) -> str:
        return (request.headers.get(TWITCH_MESSAGE_ID, '') +
                request.headers.get(TWITCH_MESSAGE_TIMESTAMP, '') +
                request.data.decode('utf-8'))

    def _get_hmac(self, secret: str, message: str) -> str:
        return hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()

    def check_request_is_a_challenge(self, request: requests.Request) -> bool:
        return MESSAGE_TYPE_CHALLENGE == request.headers.get(MESSAGE_TYPE, '')

    def verify_twitch_signature(self, request: requests.Request) -> bool:
        message = self._get_hmac_message(request)
        hmac_value = HMAC_PREFIX + self._get_hmac(self.bot.config.TWITCH_HMAC_SECRET, message)
        twitch_signature = request.headers.get(TWITCH_MESSAGE_SIGNATURE, '')

        return hmac.compare_digest(hmac_value.encode('utf-8'), twitch_signature.encode('utf-8'))

    def check_auth_token(func):
        def wrapper(self, *args, **kwargs):
            current_time = time.time()
            if not self.token_expiration_time or current_time >= self.token_expiration_time:
                self.authenticate()
            return func(self, *args, **kwargs)

        return wrapper

    def authenticate(self):
        url = f"https://id.twitch.tv/oauth2/token?client_id={self.bot.config.TWITCH_CLIENT_ID}&client_secret={self.bot.config.TWITCH_SECRET}&grant_type=client_credentials"
        response = requests.post(url)
        response_data = response.json()
        self.token = response_data['access_token']
        self.token_expiration_time = time.time() + int(response_data['expires_in'])
        return self.token


    @check_auth_token
    def get_user_id_from_login(self, login: str) -> str:
        request_url = f"{TWITCH_API_URL}/users?login={login}"
        headers = {
            'Client-ID': self.bot.config.TWITCH_CLIENT_ID,
            'Authorization': f'Bearer {self.token}'
        }
        response = requests.get(request_url, headers=headers)
        if len(response.json()['data']) == 0:
            return None
        return response.json()['data'][0]['id']

    @check_auth_token
    def get_user_info(self, login: str) -> dict:
        request_url = f"{TWITCH_API_URL}/users?login={login}"
        headers = {
            'Client-ID': self.bot.config.TWITCH_CLIENT_ID,
            'Authorization': f'Bearer {self.token}'
        }
        response = requests.get(request_url, headers=headers)
        return response.json()["data"][0] if response.json()["data"] else None

    @check_auth_token
    def get_user_info_by_id(self, user_id: str) -> dict:
        request_url = f"{TWITCH_API_URL}/users?id={user_id}"
        headers = {
            'Client-ID': self.bot.config.TWITCH_CLIENT_ID,
            'Authorization': f'Bearer {self.token}'
        }
        response = requests.get(request_url, headers=headers)
        return response.json()["data"][0] if response.json()["data"] else None

    @check_auth_token
    def get_stream_info(self, login: str) -> dict:
        request_url = f"{TWITCH_API_URL}/streams?user_login={login}&first=1"
        headers = {
            'Client-ID': self.bot.config.TWITCH_CLIENT_ID,
            'Authorization': f'Bearer {self.token}'
        }
        response = requests.get(request_url, headers=headers)
        return response.json()["data"][0] if response.json()["data"] else None

    @check_auth_token
    def subscribe_to_stream_online_event(self, user_id: str) -> None:
        request_url = f"{TWITCH_API_URL}/eventsub/subscriptions"
        headers = {
            'Client-ID': self.bot.config.TWITCH_CLIENT_ID,
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }
        data = {
            "type": "stream.online",
            "version": "1",
            "condition": {
                "broadcaster_user_id": user_id
            },
            "transport": {
                "method": "webhook",
                "callback": f"{self.bot.config.WEBHOOK_URL}/twitch",
                "secret": self.bot.config.TWITCH_HMAC_SECRET
            }
        }
        response = requests.post(request_url, headers=headers, json=data)
        return response

    @check_auth_token
    def subscribe_to_stream_offline_event(self, user_id: str) -> None:
        request_url = f"{TWITCH_API_URL}/eventsub/subscriptions"
        headers = {
            'Client-ID': self.bot.config.TWITCH_CLIENT_ID,
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }
        data = {
            "type": "stream.offline",
            "version": "1",
            "condition": {
                "broadcaster_user_id": user_id
            },
            "transport": {
                "method": "webhook",
                "callback": f"{self.bot.config.WEBHOOK_URL}/twitch",
                "secret": self.bot.config.TWITCH_HMAC_SECRET
            }
        }
        response = requests.post(request_url, headers=headers, json=data)
        return response

    @check_auth_token
    def unsubscribe_from_stream_event(self, subscription_id: str) -> None:
        request_url = f"{TWITCH_API_URL}/eventsub/subscriptions?id={subscription_id}"
        headers = {
            'Client-ID': self.bot.config.TWITCH_CLIENT_ID,
            'Authorization': f'Bearer {self.token}'
        }
        response = requests.delete(request_url, headers=headers)
        return response

    @check_auth_token
    def get_subscriptions(self) -> dict:
        request_url = f"{TWITCH_API_URL}/eventsub/subscriptions"
        headers = {
            'Client-ID': self.bot.config.TWITCH_CLIENT_ID,
            'Authorization': f'Bearer {self.token}'
        }
        response = requests.get(request_url, headers=headers)
        return response.json()

    @check_auth_token
    def get_subscription_by_user_id(self, user_id: str) -> dict:
        request_url = f"{TWITCH_API_URL}/eventsub/subscriptions?user_id={user_id}"
        headers = {
            'Client-ID': self.bot.config.TWITCH_CLIENT_ID,
            'Authorization': f'Bearer {self.token}'
        }
        response = requests.get(request_url, headers=headers)
        return response.json()
