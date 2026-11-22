import datetime
from dataclasses import replace

from dateutil import parser
from flask import request
from redis.exceptions import RedisError

from app import logger
from app.constants import Commands
from app.constants import LogTypes as logconstants
from app.core import dispatch
from app.core.commands import WebhookEvent
from app.exceptions import ErrorContext
from app.integrations.youtube.client import (
    CALLBACK_TOKEN_PARAMETER,
    HUB_MODES,
    channel_of_topic,
    notice_event,
    parse_video_notice,
    video_owner,
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

    try:
        video = bot.youtube.get_video_info(notice.video_id)
    except Exception as error:
        logger.error(
            f'youtube video {notice.video_id} could not be read: {type(error).__name__}: {error}',
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=ErrorContext(
                flow="youtube_notification",
                extra={"video_id": notice.video_id, "youtube_channel_id": notice.channel_id},
            ),
            exc_info=True,
        )
        return 'YouTube did not answer', 503

    if not video:
        return _unseen_video(notice.video_id)

    if video_owner(video) != notice.channel_id:
        logger.warn(
            f'youtube notice ignored — video {notice.video_id} is not from channel {notice.channel_id}',
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return 'Video from another channel', 204

    name = notice_event(video)
    published = name == "youtube.video.published"
    fresh = check_request_type_by_publish_time(notice.published, notice.updated)
    if published and not fresh:
        logger.info('Time difference is greater than 5 minutes. No action taken.', log_type=logconstants.COMMAND_INFO_TYPE)
        return 'No action needed', 204

    reply = dispatch.webhook(
        WebhookEvent(name, notice.channel_id, replace(notice, video=video))
    )
    if reply is None:
        return 'No action needed', 204
    return reply.body, reply.status


def _unseen_video(video_id: str) -> tuple[str, int]:
    from app.core.cache import increment_redis_key_with_expiration

    try:
        deliveries = increment_redis_key_with_expiration(
            Commands.REDIS_YOUTUBE_UNSEEN_WINDOW.format(video_id=video_id),
            1,
            Commands.YOUTUBE_UNSEEN_WINDOW_SECONDS,
        )
    except RedisError as error:
        logger.warn(
            f'youtube video {video_id} is not visible yet and its deliveries cannot be counted — Redis: {type(error).__name__}. The hub will deliver it again.',
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return 'Video not visible yet', 503
    if deliveries > Commands.YOUTUBE_UNSEEN_RETRIES:
        logger.info(
            f'youtube video {video_id} is still not visible after {Commands.YOUTUBE_UNSEEN_RETRIES} deliveries. No action taken.',
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        return 'Video not visible', 204
    logger.info(
        f'youtube video {video_id} is not visible yet. The hub will deliver it again.',
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    return 'Video not visible yet', 503


def check_request_type_by_publish_time(published: str, updated: str) -> bool:
    published_dt = parser.parse(published)
    updated_dt = parser.parse(updated)
    time_difference = updated_dt - published_dt

    return time_difference <= datetime.timedelta(minutes=5)
