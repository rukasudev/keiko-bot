from flask import request

from app import logger
from app.constants import LogTypes as logconstants
from app.webhooks import webhooks


@webhooks.route('/twitch', methods=['POST'])
def twitch_webhook():
    from app import bot

    logger.info('Twitch webhook received', log_type=logconstants.COMMAND_INFO_TYPE)

    if not bot.twitch.verify_twitch_signature(request):
        logger.error('Invalid Twitch signature', log_type=logconstants.COMMAND_INFO_TYPE)
        return 'Invalid signature', 403

    logger.info('Twitch signature match', log_type=logconstants.COMMAND_INFO_TYPE)

    data = request.json

    if bot.twitch.check_request_is_a_challenge(request):
        logger.info('Twitch webhook challenge received', log_type=logconstants.COMMAND_INFO_TYPE)
        return data['challenge']

    if data.get('subscription', {}).get('type') == 'stream.online':
        from app.services.notifications_twitch import send_streamer_notifications

        streamer_name = data['event']['broadcaster_user_name']
        send_streamer_notifications(streamer_name)

    logger.info('Twitch webhook processed', log_type=logconstants.COMMAND_INFO_TYPE)

    return "Webhook processed", 200
