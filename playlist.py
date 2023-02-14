from __future__ import annotations

from logging.handlers import RotatingFileHandler
from typing import List, Tuple

import spotipy

import hook_logging
from track import Track


class Playlist():
    """Standardized storage for Spotify PlaylistObjects and SimplifiedPlaylistObjects.

    Right now this only handles the SimplifiedPlaylistObject returned in e.g. Search API responses,
    but eventually it will handle full PlaylistObjects as well.

    Without object IDs, the Search API is the easiest way to find playlists, tracks, etc. through
    the Spotify API. Search API responses omit the full details of things like track listings for
    playlists in order to save transmitted data, instead using URLs that can be queried to get the
    full list. (This code doesn't actually use the URL, but it is in there instead of TrackObjects.)
    This class uniformly stores track information regardless of the API source.
    """

    # TODO: Add a way to make this given the pl name instead of needing to use a separate function?
    def __init__(self, spotipy_session: spotipy.Spotify, pl: dict):
        self.data = pl
        self.sp = spotipy_session
        self.tracks = self.get_playlist_tracks(pl['id'])
        self.logger = hook_logging._init_logger()

    @staticmethod
    def _playlist_from_search(spotify: spotipy.Spotify, name: str) -> dict:
        """Return the dict with the playlist data from a search endpoint response"""
        playlist = spotify.search(q=f'"{name}"', type='playlist', limit=1)
        if not playlist:
            # logger.critical('Spotify search for playlist "%s" failed', name)
            raise KeyError(f'Spotify search for playlist "{name}" failed')

        try:
            playlist = playlist['playlists']['items'][0]
        except IndexError:
            # logger.critical('No playlist data returned from Spotify for "%s"', name)
            raise KeyError(f'Could not find a playlist called "{name}".') from None

        return playlist

    @classmethod
    def from_name(cls, name: str) -> Playlist:
        """Search for a playlist called :param name: and return a Playlist object for it.

        A substring match will be used to search for :param name: because of how the Spotify API
        works. An input of "roadhouse blues" can return a playlist called "my roadhouse blues," but
        not one called "roadhouse of the blues."
        """
        return Playlist(cls._playlist_from_search(name))

    # FIXME:
    @classmethod
    def from_id(cls, spotify: spotipy.Spotify, plid: str) -> Playlist:
        """Get the playlist with id :param plid: and return a Playlist object for it."""
        playlist = spotify.playlist(plid)
        if not playlist:
            # logger.critical('Spotify search for playlist with id %s failed', plid)
            raise KeyError(f'Could not find a playlist with id "{plid}"')

        return Playlist(playlist)

    @property
    def id(self) -> str:
        """Return the Playlist's 'id' data"""
        # Consider changing this to `plid` or `pid` instead to avoid class confusion?
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
        return self.get_playlist_tracks(Playlist.from_name(pl_name).id)

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
        # TODO: Too many tr*k? variable names in this scope
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
        :returns: A tuple with unique Tracks from (self.playlist, other_pl)
        """
        # Runtime complexity can absolutely be improved here
        self_td = {t.id: t for t in self.tracks}
        other_tracks = [t for t in other_pl.tracks if t.id not in self_td]

        other_td = {t.id: t for t in other_pl.tracks}
        self_tracks = [self_td[tid] for tid in self_td if tid not in other_td]

        return (self_tracks, other_tracks)

