from . import api


@api.route('/healthcheck', methods=['GET'])
def healthcheck():
    return "API is running!", 200
