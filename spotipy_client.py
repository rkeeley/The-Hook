from __future__ import annotations

import spotipy

from decouple import config, UndefinedValueError
from spotipy.oauth2 import SpotifyOAuth

import hook_logging


class SpotipyClient:
    """Wrapper for spotipy.Spotify objects to re-use clients across Playlists"""
    def __init__(self, spotify_client: spotipy.client.Spotify = None):
        # FIXME: eventually need to support a way to add other users' credentials
        # FIXME: ensure singleton connection either by having a global array of
        #        connections or by refreshing from db values if possible
        self.logger = hook_logging._init_logger(__name__)

        self._client = spotify_client
        if not self._client:
            self.logger.info('No spotipy client input; using default credentials')
            self._client = spotipy.Spotify(
                auth_manager=SpotifyOAuth(
                    scope=['playlist-read-private'],
                    open_browser=not config('HOOK_HEADLESS', default=False, cast=bool),
                )
            )

    @property
    def client(self):
        return self._client
