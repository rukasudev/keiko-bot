from flask import request

from app import logger
from app.constants import LogTypes as logconstants
from app.core import dispatch
from app.core.commands import WebhookEvent
from app.integrations.http import webhooks
from app.integrations.twitch.client import MESSAGE_TYPE, MESSAGE_TYPE_REVOCATION


@webhooks.route('/twitch', methods=['POST'])
def twitch_webhook():
    from app import bot

    if not bot.twitch.verify_twitch_signature(request):
        logger.warn('Invalid Twitch signature', log_type=logconstants.COMMAND_WARN_TYPE)
        return 'Invalid signature', 403

    data = request.json

    if bot.twitch.check_request_is_a_challenge(request):
        return data['challenge']

    subscription = data.get('subscription') or {}
    event_type = subscription.get('type')
    if request.headers.get(MESSAGE_TYPE) == MESSAGE_TYPE_REVOCATION:
        logger.warn(
            f"Twitch revoked the {event_type} subscription: {subscription.get('status')}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return "Revocation received", 200

    broadcaster = (data.get('event') or {}).get('broadcaster_user_name') or ''
    if not broadcaster:
        logger.warn(
            f"Twitch sent a {event_type} notification without a streamer",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return "Webhook processed", 200
    event = WebhookEvent(f"twitch.{event_type}", broadcaster.lower(), data)
    reply = dispatch.webhook(event)
    if reply is None:
        return "Webhook processed", 200
    return reply.body, reply.status
