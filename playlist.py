from __future__ import annotations

from typing import List, Tuple

import hook_logging

from spotipy_client import SpotipyClient
from track import Track


class Playlist():
    """Spotify playlist container and helper.

    :attr data: the raw playlist data from Spotipy, as returned by the Spotify API
    :type data: dict

    :attr sp: Spotipy client for interacting with the Spotify playlist
    :type sp: spotipy.client.Spotify

    :attr tracks: list of tracks in the playlist
    :type tracks: List[Track]

    :attr logger: logger to use for Playlist logs
    :type logger: logging.Logger
    """

    def __init__(self, spotipy_client: SpotipyClient, pl: dict):
        """
        :param spotipy_client: SpotipyClient to use when interacting with Spotipy/Spotify API
        :param pl: raw playlist response data from Spotipy
        """
        self.data = pl
        self._spotipy_client = spotipy_client
        self.sp = self._spotipy_client.client
        self.tracks = self.get_playlist_tracks(pl['id'])
        self.logger = hook_logging._init_logger(__name__)

    @staticmethod
    def _playlist_from_search(spotipy_client: SpotipyClient, name: str) -> Playlist:
        """Return the dict with the playlist data from a search endpoint response"""
        playlist = spotipy_client.client.search(q=f'"{name}"', type='playlist', limit=1)
        if not playlist:
            raise KeyError(f'Spotify search for playlist "{name}" failed')

        try:
            playlist = playlist['playlists']['items'][0]
        except IndexError:
            raise KeyError(f'Could not find a playlist called "{name}".') from None

        return Playlist(spotipy_client, playlist)

    @classmethod
    def from_name(cls, spotipy_client: SpotipyClient, name: str) -> Playlist:
        """Search for a playlist called :param name: and return a Playlist object for it.

        A substring match will be used to search for :param name: because of how the Spotify API
        works. An input of "roadhouse blues" can return a playlist called "my roadhouse blues," but
        not one called "roadhouse of the blues."
        """
        return cls._playlist_from_search(spotipy_client, name)

    @classmethod
    def from_id(cls, spotipy_client: SpotipyClient, plid: str) -> Playlist:
        """Get the playlist with id :param plid: and return a Playlist object for it."""
        playlist = spotipy_client.client.playlist(plid)
        if not playlist:
            raise KeyError(f'Could not find a playlist with id "{plid}"')

        return Playlist(spotipy_client, playlist)

    @property
    def id(self) -> str:
        """Return the Playlist's 'id' data"""
        # TODO: Consider changing this to `plid` or `pid` instead to avoid class confusion?
        return self.data['id']

    @property
    def snapshot_id(self) -> str:
        """Return the Playlist's 'snapshot_id' data"""
        return self.data['snapshot_id']

    # FIXME: what am doing with this
    def get_playlist_tracks(self, pl_id: str) -> List[Track]:
        """Return a list of all the Tracks in the playlist with id :param pl_id:

        :param pl_id: The 'id' attribute of the Playlist object
        :returns tracks: A List of Track objects in the Playlist
        """

        limit = 100
        offset = 0
        track_obj = self.sp.playlist_tracks(pl_id, limit=limit, offset=offset)
        tracks = [Track(t) for t in track_obj['items']]

        while len(tracks) < track_obj['total']:
            offset += limit
            track_obj = self.sp.playlist_tracks(pl_id, limit=limit, offset=offset)
            tracks.extend([Track(t) for t in track_obj['items']])

        return tracks

    # FIXME: what am doing with this
    def get_tracks_from_playlist_name(self, pl_name: str) -> List[Track]:
        return self.get_playlist_tracks(Playlist.from_name(self._spotipy_client, pl_name).id)

    # FIXME: what am doing with this
    def artists_and_title_list(self, tracks: List[Track], limit: int = None) -> List[str]:
        """Return a list of strings corresponding to artist names and the track title

        :param tracks: List of Track objects
        :type tracks: [Track]
        :param limit: Maximum number of strings to return
        :type limit: int, optional
        :returns: List of '<artist name(s)> - <title>' strings for Tracks in :param track_list:
        """
        lst = []
        # FIXME: variable names in this method
        for number, trk in enumerate([tr.track for tr in tracks]):
            if limit and number >= limit:
                break

            # ', '-separated list of artist names, minus the ending ', ', plus ' - ' and Track name
            lst.append(''.join([f'{a["name"]}, ' for a in trk.artists])[:-2] + f' - {trk.name}')

        return lst

    def artists_and_title_list(self, limit: int = None) -> List[str]:
        return self.artists_and_title_list(self.tracks, limit)

    def get_differences(self, other_pl: Playlist) -> Tuple[List[Track], List[Track]]:
        """Compare this Playlist's Tracks to :param other_pl:'s Tracks and return the differences.

        :param other_pl: Another Playlist object with Tracks to compare.
                         Can be the same Playlist with a different Snapshot id.
        :type other_pl: Playlist

        :returns: A tuple with lists of unique Tracks from (self.playlist, other_pl)
        :rtype: Tuple[List[Track], List[Track]
        """
        # TODO: Runtime complexity can absolutely be improved here
        self_td = {t.id: t for t in self.tracks}
        other_tracks = [t for t in other_pl.tracks if t.id not in self_td]

        other_td = {t.id: t for t in other_pl.tracks}
        self_tracks = [self_td[tid] for tid in self_td if tid not in other_td]

        return (self_tracks, other_tracks)

