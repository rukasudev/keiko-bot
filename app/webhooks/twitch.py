from flask import request

from app import logger
from app.constants import LogTypes as logconstants
from app.webhooks import webhooks
from app.webhooks.jobs import schedule_webhook_job


@webhooks.route('/twitch', methods=['POST'])
def twitch_webhook():
    from app import bot

    if not bot.twitch.verify_twitch_signature(request):
        logger.error('Invalid Twitch signature', log_type=logconstants.COMMAND_INFO_TYPE)
        return 'Invalid signature', 403

    data = request.json

    if bot.twitch.check_request_is_a_challenge(request):
        return data['challenge']

    event_type = data.get('subscription', {}).get('type')

    if event_type in ('stream.online', 'stream.offline'):
        streamer_name = data['event']['broadcaster_user_name'].lower()
        # Named here, synchronously, because everything after this line happens
        # after the request is over: the message this log produces is the only
        # one guaranteed to arrive, so it has to say who the event was about.
        logger.info(
            f"{event_type} — **{streamer_name}**",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        schedule_webhook_job(
            notification_for(event_type, streamer_name),
            f"twitch {event_type} — {streamer_name}",
        )

    return "Webhook processed", 200


def notification_for(event_type: str, streamer_name: str):
    """The fan-out each Twitch event asks for, as an unawaited coroutine."""
    from app.services.notifications_twitch import (
        handle_send_streamer_notification,
        handle_send_streamer_offline_notification,
    )

    handler = (
        handle_send_streamer_notification if event_type == 'stream.online'
        else handle_send_streamer_offline_notification
    )
    return handler(streamer_name)
