from __future__ import annotations

from datetime import datetime
from typing import List, Dict

class Track():
    """Container for Spotify Track objects to reduce the amount of identical sub-dict code around"""

    def __init__(self, track: dict):
        self.raw = track
        self._convert_datetime_strs()

    @property
    def track(self) -> dict:
        """The Track's 'track' data"""
        return self.raw['track']

    @property
    def id(self) -> str:
        """The Track's 'id' data"""
        # Consider changing this to `tid` instead
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

    def _convert_datetime_strs(self):
        """Convert iso8601 strings in the input object into Datetime objects
        so mongo can use more appropriate types
        """
        # fromisoformat doesn't support 'Z', so translate it
        no_tz = lambda date_str : date_str.replace('Z', '+00:00')

        # wanted to use dict.update, but it overwrites things I don't update
        added_at = datetime.fromisoformat(no_tz(self.raw['added_at']))
        self.raw['added_at'] = added_at

        release_date = datetime.fromisoformat(no_tz(self.raw['track']['album']))
        self.raw['track']['album']['release_date'] = release_date

