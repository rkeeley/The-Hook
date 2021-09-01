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


def get_playlist(pl_name: str) -> Playlist:
    # Not sure if this really needs to return a Playlist instead of a dict
    p = sp.search(q=pl_name, type='playlist', limit=1)
    if not p:
        # TODO: Need to differentiate from the error below
        print(f'Failed to get playlist "{pl_name}" from Spotify')
        return None

    p = p['playlists']['items'][0]
    if not p:  # Is this even possible? I feel like it would be [] if anything, not [*]
        print(f'No playlist data returned from Spotify for {pl_name}')
        return None

    return Playlist(p)


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


def get_tracks_from_playlist_name(pl_name: str) -> [dict]:
    return get_playlist_tracks(get_playlist(pl_name).pl['id'])


def artists_and_title_list(track_list: [dict], limit: int = None) -> [str]:
    """Return a list of strings corresponding to artist names and the track title

    :param track_list: List of tracks from the Spotify Playlist response
    :type track_list: dict
    :param limit: Maximum number of strings to return
    :type limit: int, optional
    :returns: List of '<artist name(s)> - <title>' strings for Tracks in the input :param track_list:
    """
    lst = []
    for n, t in enumerate([tr['track'] for tr in track_list]):
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

    def artists_and_title_list(self, limit: int = None) -> [str]:
        return artists_and_title_list(self.tracks, limit)

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

        other_td = {t['track']['id']: t for t in other_pl.tracks}
        self_tracks = [self_td[t] for t in self_td if t not in other_td]

        return (self_tracks, other_tracks)


class BotManager(commands.Cog):
    """Manages Bot configuration data and commands.

    :attr bot: Bot instance for this script
    :type bot: discord.ext.commands.bot.Bot

    :attr pl_name: Name of the playlist to be watched
    :type pl_name: str

    :attr pl: Playlist object for :attr pl_name:
    :type pl: Playlist

    :attr snap_id_fname: Name of the shapshot id file
    :type snap_id_fname: str

    :attr snap_id: The most recent snapshot seen for :attr pl:.
        This will differ from the ID saved in the :attr snap_id_fname: from when the playlist is
        queried for updates to when the updates have been posted to the channel.
    :type snap_id: str

    :attr update_channel: The Discord Channel to which playlist update messages should be sent
    :type update_channel: discord.channel.TextChannel
    """

    def __init__(self, bot):
        self.bot = bot
        self.pl_name = config('HOOK_PLAYLIST_NAME', cast=str)
        self.pl = None
        self.update_channel = None
        self.snap_id_fname = 'snapshot-id.txt'
        self.snap_id = ''

        try:
            self.snap_id_fname = config('HOOK_SNAPSHOT_ID_FILE', cast=str)
        except UndefinedValueError:
            print(f'The snapshot id file variable, HOOK_SNAPSHOT_ID_FILE, was not set. Using {self.snap_id_fname} instead.')

        if not self._set_playlist():
            raise Exception('Failed to get playlist from Spotify')  # TODO: Better exception

        # TODO: Replace with a load_snap_id() function or something
        if path_exists(self.snap_id_fname):
            with open(self.snap_id_fname, 'r') as f:
                self.snap_id = f.read()
        else:
            self._update_snapshot_id()

        self.check_for_updates.start()

    def _set_playlist(self) -> bool:
        """Get the playlist from Spotify and set self.pl to it.

        :returns: True if successful, False otherwise
        :rtype bool:
        """
        p = get_playlist(self.pl_name)
        if not p:
            return False

        self.pl = p
        return True

    def _save_snapshot_id(self):
        with open(self.snap_id_fname, 'w') as f:
            f.write(self.pl.pl['snapshot_id'])

    def _update_snapshot_id(self) -> bool:
        """Compare self.snap_id to the snapshot id of self.pl and update self.snap_id if needed.

        This will also save the snap_id to the file, but that might need to change when a database
        is added.

        This does not affect the pl attribute or any attribute besides snap_id.

        :returns: True if the snapshot id is new; False otherwise
        :rtype bool:
        """

        if not self.snap_id:
            # First time running this script or using this snapshot id file. Save the id and return
            print(f'Saving snapshot id to {self.snap_id_fname} - new file or first run')
            self._save_snapshot_id()
            return True

        # FIXME: The snapshot id comparison needs to happen after the playlist is retrieved
        if self.snap_id != self.pl.pl['snapshot_id']:
            print('Snap ids do not match')
            self.snap_id = self.pl.pl['snapshot_id']
            self._save_snapshot_id()
            return True

        print('No difference in snapshot ids. Log this or something')
        return False

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

        e = discord.embeds.Embed(
            title=track['track']['name'],
            type='rich',
            description=f'{artists} • *{track["track"]["album"]["name"]}*',
            url=track['track']['album']['external_urls']['spotify'],
            timestamp=datetime.fromisoformat(track['added_at'][:-1]),  # [:-1] to remove 'Z'ms
            color=color,
        ).set_thumbnail(
            url=track['track']['album']['images'][0]['url'],
        ).set_author(
            # TODO: add "by {user}" (if new?) in case there's no pfp or it's not obvious who did it
            name='Song {} "{}"'.format('added to' if new else 'removed from', pl_name),
            url=track['track']['external_urls']['spotify'],
            # FIXME: Not sure if available for deleted tracks
            icon_url=sp.user(track['added_by']['id'])['images'][0]['url'] if new else discord.Embed.Empty,
        )

        if new:
            e.add_field(
                name='Potential Genres',
                value=' • '.join(random.sample(genres, min(4, len(genres)))),
            )

        return e

    @commands.command(name='embed')
    async def embed_test(self, ctx):
        # TODO: Testing with one track for now, but this needs to be expanded
        await ctx.send(embed=self._embed_from_track(self.pl.tracks[0]))

    @commands.command(name='playlist')
    async def playlist(self, ctx):
        await ctx.send(self.pl.pl['external_urls']['spotify'])

    @commands.command(name='check')
    async def check(self, ctx):
        await self.check_for_updates()

    @commands.command(name='pdb', hidden=True)
    async def pdb(self, ctx):
        """Drop the process running the bot into pdb"""
        breakpoint()
        print('Entered pdb')

    @tasks.loop(minutes=1.0)
    async def check_for_updates(self):
        """Check for and notify about playlist updates once every 20 minutes."""
        p = get_playlist(self.pl_name)
        if p.pl['snapshot_id'] != self.snap_id:
            # Snapshot ids differ. Need to send updates and then save the new pl
            # FIXME: For some reason the updated tracks aren't being returned here :\
            print('check_for_updates: snapshot ids differ')

            removed_tracks, new_tracks = self.pl.get_differences(p)
            for t in removed_tracks:
                await self.update_channel.send(embed=self._embed_from_track(t, new=False))

            for t in new_tracks:
                await self.update_channel.send(embed=self._embed_from_track(t))

            # FIXME: self.pl is updated, but not self.pl.tracks?
            self.pl = p
            self._update_snapshot_id()

    @check_for_updates.before_loop
    async def before_bot_ready(self):
        # Need to wait until the bot is running to get the Channel info
        await self.bot.wait_until_ready()
        self.update_channel = [c for c in self.bot.get_all_channels()
                               if c.name == config('HOOK_UPDATE_CHANNEL')][0]


@bot.event
async def on_ready():
    status = u'\N{musical note}\N{page with curl}'
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=status))


bot.add_cog(BotManager(bot))
bot.run(bot_token)
