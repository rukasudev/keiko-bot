import datetime

from dateutil import parser
from flask import request

from app import logger
from app.constants import Commands as commands_constants
from app.constants import LogTypes as logconstants
from app.exceptions import ErrorContext
from app.integrations.youtube import (
    CALLBACK_TOKEN_PARAMETER,
    HUB_MODES,
    channel_of_topic,
    parse_video_notice,
)
from app.webhooks import webhooks
from app.webhooks.jobs import schedule_webhook_job


@webhooks.route('/youtube', methods=['GET', 'POST'])
def youtube_webhook():
    from app import bot
    from app.services.cache import claim_redis_key, release_redis_key
    from app.services.notifications_youtube_video import (
        announce_video,
        prepare_video_announcement,
    )

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

    context = ErrorContext(
        flow="youtube_notification",
        extra={"video_id": notice.video_id, "youtube_channel_id": notice.channel_id},
    )

    try:
        announcement = prepare_video_announcement(notice.video_id, notice.channel_id)
    except Exception as error:
        logger.error(
            f'youtube video {notice.video_id} could not be read: {type(error).__name__}: {error}',
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        return 'YouTube did not answer', 503

    if announcement is None:
        logger.info(
            f'youtube video {notice.video_id} is not visible yet. The hub will deliver it again.',
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        return 'Video not visible yet', 503

    if announcement.owner != notice.channel_id:
        logger.warn(
            f'youtube notice ignored — video {notice.video_id} is not from channel {notice.channel_id}',
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return 'Video from another channel', 204

    claim = commands_constants.REDIS_YOUTUBE_NOTIFIED_VIDEO.format(video_id=notice.video_id)
    if not claim_redis_key(claim, commands_constants.YOUTUBE_NOTIFIED_VIDEO_TTL_SECONDS):
        logger.info(
            f'youtube video {notice.video_id} was already announced. No action taken.',
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        return 'Already announced', 204

    logger.info(
        f'new video {notice.video_id} — **{announcement.youtuber}**',
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    try:
        schedule_webhook_job(
            announce_video(announcement),
            f'youtube video — {announcement.youtuber}',
        )
    except Exception as error:
        release_redis_key(claim)
        logger.error(
            f'youtube video {notice.video_id} could not be handed to the bot: {type(error).__name__}: {error}',
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        return 'Announcement not scheduled', 503

    return 'Webhook processed', 204

def check_request_type_by_publish_time(published: str, updated: str) -> bool:
    published_dt = parser.parse(published)
    updated_dt = parser.parse(updated)
    time_difference = updated_dt - published_dt

    return time_difference <= datetime.timedelta(minutes=5)
