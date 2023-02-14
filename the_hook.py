from __future__ import annotations

import spotipy

from decouple import config, UndefinedValueError
from spotipy.oauth2 import SpotifyOAuth

import hook_bot
import hook_logging

# FIXME: This needs to be tied to individual users eventually if this script is to become a real
#        bot. It's global for now because only my account is used and the code is simpler this way.
sp = spotipy.Spotify(
    auth_manager=SpotifyOAuth(
        scope=['playlist-read-private'],
        open_browser=not config('HOOK_HEADLESS', default=False, cast=bool)
    )
)

bot_token = config('HOOK_BOT_TOKEN')
bot = hook_bot.initialize_bot()
logger = hook_logging._init_logger()
logger.info('Starting bot with prefix "%s" and check interval %f', bot_prefix, bot_check_interval)
bot.run(bot_token)
