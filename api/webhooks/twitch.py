from flask import jsonify, request
from utils import verify_twitch_signature

from . import webhooks


@webhooks.route('/twitch', methods=['GET'])
def twitch_webhook():
    if not verify_twitch_signature(request):
        return 'Invalid signature', 403

    data = request.json

    if 'challenge' in data:
        return jsonify({
            'challenge': data['challenge']
        })

    if data.get('subscription', {}).get('type') == 'stream.online':
        streamer_name = data['event']['broadcaster_user_name']
        print(f"{streamer_name} começou a transmitir!")

    return "Webhook processed", 200
