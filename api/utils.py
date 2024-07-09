import hashlib
import hmac

from config import Config as config


def verify_twitch_signature(request):
    message = request.headers['Twitch-Eventsub-Message-Id'] + request.headers['Twitch-Eventsub-Message-Timestamp'] + request.get_data(as_text=True)
    secret = config.TWITCH_SECRET_KEY.encode('utf-8')
    signature = hmac.new(secret, message.encode('utf-8'), hashlib.sha256).hexdigest()
    expected_signature = request.headers['Twitch-Eventsub-Message-Signature'].split('=')[1]
    return hmac.compare_digest(signature, expected_signature)
