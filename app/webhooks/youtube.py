import datetime
import re

from dateutil import parser
from flask import request

from app import logger
from app.constants import LogTypes as logconstants
from app.webhooks import webhooks

pattern = r'<yt:videoId>(.*?)</yt:videoId>[\s\S]*<yt:channelId>(.*?)</yt:channelId>[\s\S]*<published>(.*?)</published>[\s\S]*<updated>(.*?)</updated>'

@webhooks.route('/youtube', methods=['GET', 'POST'])
def youtube_webhook():
    challenge = request.args.get('hub.challenge')
    if challenge:
        return challenge

    data = request.data.decode('utf-8')

    match = re.search(pattern, data, re.DOTALL)
    if not match:
        logger.error('Invalid Youtube webhook data', log_type=logconstants.COMMAND_INFO_TYPE)
        return 'Invalid request data', 403

    video_id, channel_id, published_str, updated_str = match.groups()

    from app.services.notifications_youtube_video import send_youtube_video_notification

    if not check_request_type_by_publish_time(published_str, updated_str):
        logger.info('Time difference is greater than 5 minutes. No action taken.', log_type=logconstants.COMMAND_INFO_TYPE)
        return 'No action needed', 204

    send_youtube_video_notification(video_id, channel_id)

    return 'Webhook processed', 204

def check_request_type_by_publish_time(published: str, updated: str) -> bool:
    published_dt = parser.parse(published)
    updated_dt = parser.parse(updated)
    time_difference = updated_dt - published_dt

    return time_difference <= datetime.timedelta(minutes=5)
