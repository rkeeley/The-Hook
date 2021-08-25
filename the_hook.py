from __future__ import annotations

import asyncio
import discord
import random
import spotipy

from datetime import datetime
from decouple import config, UndefinedValueError
from discord.ext import commands, tasks
from os.path import exists as path_exists
from spotipy.oauth2 import SpotifyOAuth


bot_token = config('HOOK_BOT_TOKEN')
bot_prefix = config('HOOK_BOT_PREFIX', default='.', cast=str)
intents = discord.Intents.default()
intents.presences = False

bot = commands.Bot(intents=intents, command_prefix=bot_prefix)

# FIXME: This needs to be tied to individual users eventually if this script is to become a real
#        bot. It's global for now because only my account is used and the code is simpler this way.
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(scope=['playlist-read-private']))


def get_playlist_tracks(pl_id: str) -> [dict]:
    """Return a list of all the tracks in the playlist with id `pl_id`

    :param pl_id: The 'id' attribute of the Playlist object
    :returns tracks: A List of Track objects in the Playlist
    """

    limit = 100
    offset = 0
    track_obj = sp.playlist_tracks(pl_id, limit=limit, offset=offset)
    tracks = track_obj['items']

    while len(tracks) < track_obj['total']:
        offset += limit
        track_obj = sp.playlist_tracks(pl_id, limit=limit, offset=offset)
        tracks.extend(track_obj['items'])

    return tracks


def artists_and_title_list(playlist: Playlist, limit: int = None) -> [str]:
    """Return a list of strings corresponding to artist names and the track title

    :param playlist: Playlist object containing Tracks
    :param limit: Maximum number of strings to return
    :type limit: int, optional
    :returns: List of '<artist name(s)> - <title>' strings for Tracks in the stored Playlist
    """
    lst = []
    for n, t in enumerate([tr['track'] for tr in playlist.tracks]):
        if limit and n >= limit:
            break

        # ', '-separated list of artist names, minus the ending ', ', plus ' - ' and Track name
        lst.append(''.join([f'{a["name"]}, ' for a in t['artists']])[:-2] + f' - {t["name"]}')

    return lst


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

    def __init__(self, pl: dict):
        self.pl = pl
        self.tracks = get_playlist_tracks(pl['id'])

    # TODO: Remove? This was originally here, but it's useful for Tracks on their own
    def artists_and_title_list(self, limit: int = None) -> [str]:
        return artists_and_title_list(self, limit)

    def get_differences(self, other_pl: Playlist) -> ([dict], [dict]):
        """Compare this Playlist's Tracks to `other_pl`'s Tracks and return the differences.

        :param other_pl: Another Playlist object with Tracks to compare.
                         Can be the same Playlist with a different Snapshot id.
        :type other_pl: Playlist
        :returns: A tuple with dicts of unique Tracks from this playlist and `other_pl`, respectively
        """
        # Runtime complexity can absolutely be improved here
        self_td = {t['track']['id']: t for t in self.tracks}
        other_tracks = [t for t in other_pl.tracks if t['track']['id'] not in self_td]
        self_tracks = [self_td[t] for t in self_td if t not in other_tracks]
        return (self_tracks, other_tracks)


class BotManager(commands.Cog):
    """Manages bot configuration data"""

    def __init__(self, bot):
        self.bot = bot
        self.pl_name = config('HOOK_PLAYLIST_NAME', cast=str)
        self.pl = None
        self.snap_id_fname = 'snapshot-id.txt'
        self.snap_id = ''

        try:
            self.snap_id_fname = config('HOOK_SNAPSHOT_ID_FILE', cast=str)
        except UndefinedValueError:
            print(f'The snapshot id file variable, HOOK_SNAPSHOT_ID_FILE, was not set. Using {self.snap_id_fname} instead.')

        if path_exists(self.snap_id_fname):
            with open(self.snap_id_fname, 'r') as f:
                self.snap_id = f.read()
        else:
            print(f'No file {self.snap_id_fname} found. The snapshot id will be saved to {self.snap_id_fname}.')

        if not self._get_playlist():
            raise Exception('Failed to get playlist from Spotify')  # TODO: Better exception

    def _get_playlist(self) -> bool:
        """Get the playlist object from Spotify. Returns True if successful; False otherwise.

        I should eventually figure out how to turn this into a bot command
        """
        p = sp.search(q=self.pl_name, type='playlist', limit=1)
        if not p:
            # TODO: Need to differentiate this error from the one below
            print('Failed to get playlist from Spotify')
            return False

        p = p['playlists']['items'][0]
        if not p:
            print('No playlist data returned from Spotify')
            return False

        self.pl = Playlist(p)
        return True

    def _update_snapshot_id(self) -> bool:
        """Compare internal snapshot ids and send updates if they differ.

        This function assumes that _get_playlist has been called since the previous snapshot id update.

        Not sure what I want to do with params (e.g. don't send message) or return value yet.
        """

        if not self.snap_id:
            # First time running this script or using this snapshot id file. Save the id and return
            print(f'Saving snapshot id to {self.snap_id_fname} - new file or first run')
            with open(self.snap_id_fname, 'w') as f:
                f.write(self.pl.pl['snapshot_id'])
            return True

        if self.snap_id != self.pl.pl['snapshot_id']:
            # TODO: Call function to prepare and/or send message
            print('This is when a message would be sent with the playlist differences')
        else:
            print('No difference in snapshot ids. Log this or something')
            return False

        self.snap_id = self.pl.pl['snapshot_id']
        # TODO: Write update to the snap id file
        return True

    def _embed_from_track(self, track: dict, new=True, pl_name=None) -> discord.embeds.Embed:
        """Testing embeds

        :param track: Spotify Track dict for the embedded song. Not just the ['track'] part.
        :type track: dict (Track object)
        :param new: Whether this embed is about a newly-added Track or a removed one
        :type new: bool, optional
        """
        # TODO: There are two API calls in this function. See if they can be made redundant
        artists = ''.join([f'**{a["name"]}**, ' for a in track['track']['artists']])[:-2]
        # Spotify green, or some red-ish analogoue of its purple-ish tetradic color
        color = discord.Color.from_rgb(30, 215, 96) if new else discord.Color.from_rgb(186, 30, 53)
        genres = sp.artist(track['track']['artists'][0]['id'])['genres']
        pl_name = pl_name or self.pl_name

        return discord.embeds.Embed(
            title=track['track']['name'],
            type='rich',
            description=f'{artists} • *{track["track"]["album"]["name"]}*',
            url=track['track']['album']['external_urls']['spotify'],
            timestamp=datetime.fromisoformat(track['added_at'][:-1]),  # [:-1] to remove 'Z'ms
            color=color,
        ).add_field(
            name='Potential Genres',
            value=' • '.join(random.sample(genres, min(4, len(genres)))),
        ).set_thumbnail(
            url=track['track']['album']['images'][0]['url'],
        ).set_author(
            name='Song {} "{}"'.format('added to' if new else 'removed from', pl_name),
            url=track['track']['external_urls']['spotify'],
            # FIXME: Not sure if available for deleted tracks
            icon_url=sp.user(track['added_by']['id'])['images'][0]['url'],
        )

    @commands.command(name='embed')
    async def embed_test(self, ctx):
        # TODO: Testing with one track for now, but this needs to be expanded
        await ctx.send(embed=self._embed_from_track(self.pl.tracks[0]))

    @commands.command(name='playlist')
    async def playlist(self, ctx):
        await ctx.send(self.pl.pl['external_urls']['spotify'])

    @tasks.loop(minutes=20.0)
    async def check_for_updates(self):
        """Check for and notify about playlist updates once every 20 minutes."""
        if self._get_playlist() and self._update_snapshot_id():
            # TODO: ? Maybe don't call it like this
            print('updated playlist and snapshot id')
        print('checked for updates just now')
        print('\n'.join(self.pl.artists_and_title_list()))  # Can't ctx.send here because no context


@bot.event
async def on_ready():
    status = u'\N{musical note}\N{page with curl}  \N{eyes}'
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=status))


bot.add_cog(BotManager(bot))
bot.get_cog('BotManager').check_for_updates.start()
bot.run(bot_token)
