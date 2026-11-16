import datetime

from dateutil import parser
from flask import request

from app import logger
from app.constants import LogTypes as logconstants
from app.core import dispatch
from app.core.commands import WebhookEvent
from app.exceptions import ErrorContext
from app.integrations.youtube.client import (
    CALLBACK_TOKEN_PARAMETER,
    HUB_MODES,
    channel_of_topic,
    parse_video_notice,
)
from app.integrations.http import webhooks


@webhooks.route('/youtube', methods=['GET', 'POST'])
def youtube_webhook():
    from app import bot

    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        channel_id = channel_of_topic(request.args.get('hub.topic'))
        challenge = request.args.get('hub.challenge')
        from_the_hub = bot.youtube.callback_token_matches(
            channel_id, request.args.get(CALLBACK_TOKEN_PARAMETER)
        )

        if from_the_hub and mode == 'denied':
            logger.error(
                f'youtube subscription of channel {channel_id} denied by the hub',
                log_type=logconstants.COMMAND_ERROR_TYPE,
                context=ErrorContext(
                    flow="youtube_subscription", extra={"youtube_channel_id": channel_id}
                ),
            )
            return 'Not confirmed', 404

        if mode not in HUB_MODES or not challenge or not from_the_hub:
            named_mode = mode if mode in HUB_MODES else 'an unknown mode'
            subject = f'channel {channel_id}' if channel_id else 'an unknown channel'
            logger.info(
                f'youtube verification refused — {named_mode} of {subject}: **Keiko** did not ask for it',
                log_type=logconstants.COMMAND_INFO_TYPE,
            )
            return 'Not confirmed', 404
        return challenge, 200, {'Content-Type': 'text/plain; charset=utf-8'}

    token = request.args.get(CALLBACK_TOKEN_PARAMETER)
    if not token:
        logger.info(
            'youtube notice on a callback without a token — answered 410 so the hub ends that subscription',
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        return 'Gone', 410

    body = request.get_data()
    if not bot.youtube.verify_hub_signature(body, request.headers.get('X-Hub-Signature', '')):
        logger.warn(
            'youtube notice refused — missing or wrong hub signature',
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return 'Invalid signature', 403

    notice = parse_video_notice(body)
    if notice is None:
        logger.info('youtube notice names no video. No action taken.', log_type=logconstants.COMMAND_INFO_TYPE)
        return 'No video in this notice', 204

    if not bot.youtube.callback_token_matches(notice.channel_id, token):
        logger.warn(
            f'youtube notice refused — channel {notice.channel_id} came on the callback of another channel',
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return 'Invalid token', 403

    if not check_request_type_by_publish_time(notice.published, notice.updated):
        logger.info('Time difference is greater than 5 minutes. No action taken.', log_type=logconstants.COMMAND_INFO_TYPE)
        return 'No action needed', 204

    reply = dispatch.webhook(
        WebhookEvent("youtube.video.published", notice.channel_id, notice)
    )
    if reply is None:
        return 'No action needed', 204
    return reply.body, reply.status


def check_request_type_by_publish_time(published: str, updated: str) -> bool:
    published_dt = parser.parse(published)
    updated_dt = parser.parse(updated)
    time_difference = updated_dt - published_dt

    return time_difference <= datetime.timedelta(minutes=5)
