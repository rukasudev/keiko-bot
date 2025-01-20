from flask import request

from app import logger
from app.constants import LogTypes as logconstants
from app.webhooks import webhooks


@webhooks.route('/twitch', methods=['POST'])
def twitch_webhook():
    from app import bot

    if not bot.twitch.verify_twitch_signature(request):
        logger.error('Invalid Twitch signature', log_type=logconstants.COMMAND_INFO_TYPE)
        return 'Invalid signature', 403

    data = request.json

    if bot.twitch.check_request_is_a_challenge(request):
        return data['challenge']

    if data.get('subscription', {}).get('type') == 'stream.online':
        from app.services.notifications_twitch import handle_send_streamer_notification

        streamer_name = data['event']['broadcaster_user_name'].lower()
        bot.loop.create_task(handle_send_streamer_notification(streamer_name))

    if data.get('subscription', {}).get('type') == 'stream.offline':
        from app.services.notifications_twitch import (
            handle_send_streamer_offline_notification,
        )

        streamer_name = data['event']['broadcaster_user_name'].lower()
        logger.info(f"Stream offline event received for {streamer_name}", log_type=logconstants.COMMAND_INFO_TYPE)
        bot.loop.create_task(handle_send_streamer_offline_notification(streamer_name))

    return "Webhook processed", 200
