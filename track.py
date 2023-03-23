from __future__ import annotations

from datetime import datetime
from typing import List

class Track():
    """Spotify Track container and helper.

    :attr raw: raw response data from Spotipy for the Spotify track
    :type raw: dict
    """

    def __init__(self, track: dict):
        """
        :param track: raw response data from Spotipy for the Spotify track
        """
        self.raw = track
        self._convert_datetime_strs()

    @property
    def track(self) -> dict:
        """The Track's 'track' data"""
        return self.raw['track']

    @property
    def id(self) -> str:
        """The Track's 'id' data"""
        # TODO: Consider changing this to `tid` instead
        return self.track['id']

    @property
    def artists(self) -> List[dict]:
        """The Track's 'artists' data"""
        return self.track['artists']

    @property
    def name(self) -> str:
        """The Track's 'name' data"""
        return self.track['name']

    @property
    def album(self) -> List[dict]:
        """The Track's 'album' data"""
        return self.track['album']

    def _convert_datetime_strs(self) -> None:
        """Convert iso8601 strings in the input object into Datetime objects in place"""
        # fromisoformat doesn't support 'Z', so translate it
        no_tz = lambda date_str : date_str.replace('Z', '+00:00')

        if isinstance(self.raw['added_at'], str):
            added_at = datetime.fromisoformat(no_tz(self.raw['added_at']))
            self.raw['added_at'] = added_at
        elif not isinstance(self.raw['added_at'], datetime.datetime):
            raise AttributeError(f"Unexpected added_at type {type(self.raw['added_at'])}")

        if self.raw['track']['album']['release_date_precision'] == 'day':
            release_date = datetime.fromisoformat(no_tz(self.raw['track']['album']['release_date']))
            self.raw['track']['album']['release_date'] = release_date

