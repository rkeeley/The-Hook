from decouple import config

import hook_bot
import hook_logging

from spotipy_client import SpotipyClient


if __name__ == '__main__':
    bot_check_interval = config('HOOK_CHECK_INTERVAL', default=60.0, cast=float)
    bot_prefix = config('HOOK_BOT_PREFIX', default='.', cast=str)
    bot_token = config('HOOK_BOT_TOKEN')

    spotipy_client = SpotipyClient()

    bot = hook_bot.initialize_bot(
        prefix=bot_prefix,
        check_interval=bot_check_interval,
        spotipy_client=spotipy_client)

    logger = hook_logging._init_logger('The Hook')
    logger.info('Starting bot with prefix "%s" and check interval %f', bot_prefix, bot_check_interval)

    bot.run(bot_token)
