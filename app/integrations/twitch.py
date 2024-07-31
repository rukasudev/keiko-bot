import hashlib
import hmac

TWITCH_MESSAGE_ID = 'Twitch-Eventsub-Message-Id'.lower()
TWITCH_MESSAGE_TIMESTAMP = 'Twitch-Eventsub-Message-Timestamp'.lower()
TWITCH_MESSAGE_SIGNATURE = 'Twitch-Eventsub-Message-Signature'.lower()
HMAC_PREFIX = 'sha256='

MESSAGE_TYPE = 'Twitch-Eventsub-Message-Type'.lower()
MESSAGE_TYPE_CHALLENGE = 'webhook_callback_verification'

class TwitchClient:
    def __init__(self, bot):
        self.bot = bot
        self.token = None
        self.token_expiration = None

    def get_hmac_message(self, request):
        return (request.headers.get(TWITCH_MESSAGE_ID, '') +
                request.headers.get(TWITCH_MESSAGE_TIMESTAMP, '') +
                request.data.decode('utf-8'))

    def get_hmac(self, secret, message):
        return hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()

    def check_request_is_a_challenge(self, request):
        return MESSAGE_TYPE_CHALLENGE == request.headers.get(MESSAGE_TYPE, '')

    def verify_twitch_signature(self, request):
        message = self.get_hmac_message(request)
        hmac_value = HMAC_PREFIX + self.get_hmac(self.bot.config.TWITCH_HMAC_SECRET, message)
        twitch_signature = request.headers.get(TWITCH_MESSAGE_SIGNATURE, '')

        return hmac.compare_digest(hmac_value.encode('utf-8'), twitch_signature.encode('utf-8'))
