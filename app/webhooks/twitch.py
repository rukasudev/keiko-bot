from flask import jsonify, request

from app import bot, logger
from app.services.utils import verify_twitch_signature
from app.webhooks import webhooks


@webhooks.route('/twitch', methods=['GET'])
def twitch_webhook():
    logger.info('Twitch webhook received')
    logger.info("Request headers: %s", request.headers)
    logger.info("Request data: %s", request.data)

    if not bot.twitch.verify_twitch_signature(request):
        return 'Invalid signature', 403

    logger.info('Twitch signature match')

    data = request.json

    if bot.twitch.check_request_is_a_challenge(request):
        logger.info('Twitch webhook challenge received')
        return data['challenge']

    channel = bot.get_channel(bot.config.ADMIN_LOGS_CHANNEL_ID)

    if data.get('subscription', {}).get('type') == 'stream.online':
        streamer_name = data['event']['broadcaster_user_name']
        logger.info(f"{streamer_name} começou a transmitir!")
        bot.loop.create_task(channel.send(f"{streamer_name} começou a transmitir!"))


    return "Webhook processed", 200

