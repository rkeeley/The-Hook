from __future__ import annotations

import logging
import random

from datetime import datetime
from logging.handlers import RotatingFileHandler
from os.path import exists as path_exists

import discord
import spotipy

from decouple import config, UndefinedValueError
from discord.ext import commands, tasks
from spotipy.oauth2 import SpotifyOAuth


bot_token = config('HOOK_BOT_TOKEN')
bot_log_file = config('HOOK_LOG_FILE', default='the_hook.log', cast=str)
bot_prefix = config('HOOK_BOT_PREFIX', default='.', cast=str)
bot_check_interval = config('HOOK_CHECK_INTERVAL', default=20.0, cast=float)
DEBUG = config('HOOK_DEBUG', default=False, cast=bool)

logger = logging.getLogger('the_hook')
logger.setLevel(logging.INFO)  # TODO: Parameterize
log_handler = RotatingFileHandler(filename=bot_log_file, encoding='utf-8', mode='a')
log_handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(log_handler)

intents = discord.Intents.default()
intents.presences = False
bot = commands.Bot(intents=intents, command_prefix=bot_prefix)

# FIXME: This needs to be tied to individual users eventually if this script is to become a real
#        bot. It's global for now because only my account is used and the code is simpler this way.
sp = spotipy.Spotify(
    auth_manager=SpotifyOAuth(
        scope=['playlist-read-private'],
        open_browser=not config('HOOK_HEADLESS', default=False, cast=bool)
    )
)


def get_playlist_tracks(pl_id: str) -> [Track]:
    """Return a list of all the Tracks in the playlist with id :param pl_id:

    :param pl_id: The 'id' attribute of the Playlist object
    :returns tracks: A List of Track objects in the Playlist
    """

    limit = 100
    offset = 0
    track_obj = sp.playlist_tracks(pl_id, limit=limit, offset=offset)
    tracks = [Track(t) for t in track_obj['items']]

    while len(tracks) < track_obj['total']:
        offset += limit
        track_obj = sp.playlist_tracks(pl_id, limit=limit, offset=offset)
        tracks.extend([Track(t) for t in track_obj['items']])

    return tracks


def get_tracks_from_playlist_name(pl_name: str) -> [Track]:
    return get_playlist_tracks(Playlist.from_name(pl_name).id)


def artists_and_title_list(tracks: [Track], limit: int = None) -> [str]:
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


class Track():
    """Container for Spotify Track objects to reduce the amount of identical sub-dict code around"""

    def __init__(self, track: dict):
        self.raw = track

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
    def artists(self) -> [dict]:
        """The Track's 'artists' data"""
        return self.track['artists']

    @property
    def name(self) -> str:
        """The Track's 'name' data"""
        return self.track['name']

    @property
    def album(self) -> [dict]:
        """The Track's 'album' data"""
        return self.track['album']


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
    def __init__(self, pl: dict):
        self.data = pl
        self.tracks = get_playlist_tracks(pl['id'])

    @staticmethod
    def _playlist_from_search(name: str) -> dict:
        """Return the dict with the playlist data from a search endpoint response"""
        playlist = sp.search(q=f'"{name}"', type='playlist', limit=1)
        if not playlist:
            logger.critical('Spotify search for playlist "%s" failed', name)
            raise KeyError(f'Spotify search for playlist "{name}" failed')

        try:
            playlist = playlist['playlists']['items'][0]
        except IndexError:
            logger.critical('No playlist data returned from Spotify for "%s"', name)
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

    @classmethod
    def from_id(cls, plid: str) -> Playlist:
        """Get the playlist with id :param plid: and return a Playlist object for it."""
        playlist = sp.playlist(plid)
        if not playlist:
            logger.critical('Spotify search for playlist with id %s failed', plid)
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

    def artists_and_title_list(self, limit: int = None) -> [str]:
        return artists_and_title_list(self.tracks, limit)

    def get_differences(self, other_pl: Playlist) -> ([Track], [Track]):
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


class HookBot(commands.Cog):
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
        self.pl_name: str = config('HOOK_PLAYLIST_NAME', cast=str)
        self.pl: Playlist = None
        self.update_channel = None
        self.snap_id_fname: str = 'snapshot-id.txt'
        self.snap_id: str = ''

        try:
            self.snap_id_fname = config('HOOK_SNAPSHOT_ID_FILE', cast=str)
        except UndefinedValueError:
            logger.warning(
                'The snapshot id file variable, HOOK_SNAPSHOT_ID_FILE, was not set. Using "%s".',
                self.snap_id_fname)

        if not self._set_playlist():
            logger.critical('Failed to get the "%s" playlist from Spotify.', self.pl_name)
            raise Exception('Failed to get playlist from Spotify')

        # Set :attr snap_id: to the known snapshot_id, or create the :attr snap_id_fname: file if it
        # doesn't exist/this is the first run
        if not self._load_snapshot_id():
            self._update_snapshot_id()

        self.check_for_updates.start()

    def _get_playlist(self) -> Playlist:
        """Get a Playlist object using self.pl.id if available and self.pl_name otherwise."""
        try:
            if self.pl and self.pl.id:
                playlist = Playlist.from_id(self.pl.id)
            else:
                playlist = Playlist.from_name(self.pl_name)
        except KeyError:
            logger.critical('_get_playlist: Failed to get playlist from Spotify')
            return None

        return playlist

    def _set_playlist(self, playlist: Playlist = None) -> bool:
        """Get the playlist from Spotify and set self.pl to it.

        :returns: True if successful, False otherwise
        :rtype bool:
        """
        if playlist:
            self.pl = playlist
            return True

        # FIXME: I want to do something to confirm with the user that the correct playlist has been
        #        found
        playlist = self._get_playlist()
        if not playlist:
            logger.critical('_set_playlist: Failed to get playlist from Spotify')
            return False

        self.pl = playlist
        return True

    def _load_snapshot_id(self):
        """Sets :attr self.snap_id: to the watched Playlist's snapshot_id.

        :returns: True if the snapshot_id was updated; False otherwise
        :rtype bool:
        """
        if path_exists(self.snap_id_fname):
            with open(self.snap_id_fname, 'r', encoding='utf-8') as f:
                self.snap_id = f.read()
                return True

        return False

    def _save_snapshot_id(self):
        with open(self.snap_id_fname, 'w', encoding='utf-8') as f:
            f.write(self.pl.snapshot_id)

    def _update_snapshot_id(self) -> bool:
        """Compare :attr snap_id: to the snapshot id of :attr pl: and update the former if needed.

        This will also save the snap_id to the file, but that might need to change when a database
        is added.

        This does not affect the :attr pl: attribute or any attribute besides :attr snap_id:.

        :returns: True if the snapshot id is new; False otherwise
        :rtype bool:
        """

        if not self.snap_id:
            # First time running this script or using this snapshot id file. Save the id and return
            logger.info('Saving snapshot id to %s - new file or first run', self.snap_id_fname)
            self._save_snapshot_id()
            return True

        if self.snap_id != self.pl.snapshot_id:
            logger.info('_update_snapshot_id: Snap ids do not match')
            self.snap_id = self.pl.snapshot_id
            self._save_snapshot_id()
            return True

        logger.info('_update_snapshot_id: No difference in snapshot ids.')
        return False

    def _embed_from_track(self, track: Track, new=True, pl_name=None) -> discord.embeds.Embed:
        """Creates a formatted Embed object using :param track: data for a single Discord message.

        :param track: Track object for the song to be embedded
        :type track: Track
        :param new: Whether this embed is for a newly-added Track or a removed one
            Embeds for added (new) and removed (not new) tracks contain different information.
        :type new: bool, optional
        """
        # TODO: There are two API calls in this function. See if they can be made redundant
        artists = ''.join([f'**{a["name"]}**, ' for a in track.artists])[:-2]
        # Spotify green, or some red-ish analogoue of its purple-ish tetradic color
        color = discord.Color.from_rgb(30, 215, 96) if new else discord.Color.from_rgb(186, 30, 53)
        genres = sp.artist(track.artists[0]['id'])['genres']
        pl_name = pl_name or self.pl_name

        embed = discord.embeds.Embed(
            title=track.name,
            type='rich',
            description=f'{artists} • *{track.album["name"]}*',
            url=track.album['external_urls']['spotify'],
            timestamp=datetime.fromisoformat(track.raw['added_at'][:-1]),  # [:-1] to remove 'Z'ms
            color=color,
        ).set_thumbnail(
            url=track.album['images'][0]['url'],
        ).set_author(
            # TODO: add "by {user}" (if new?) in case there's no pfp or it's not obvious who did it
            name=f'Song {"added to" if new else "removed from"} "{pl_name}"',
            url=track.raw['track']['external_urls']['spotify'],
            icon_url=(discord.Embed.Empty if not new
                      else sp.user(track.raw['added_by']['id'])['images'][0]['url']),
        )

        if new and genres:
            embed.add_field(
                name='Artist Genres',
                value=' • '.join(random.sample(genres, min(4, len(genres)))),
            )

        return embed

    @commands.command(name='embed')
    async def embed_first_track(self, ctx):
        await ctx.send(embed=self._embed_from_track(self.pl.tracks[0]))

    @commands.command(name='playlist', aliases=['pl'])
    async def playlist(self, ctx):
        await ctx.send(self.pl.data['external_urls']['spotify'])

    @commands.command(name='check')
    async def check(self, ctx):
        async with ctx.typing():
            msg = await ctx.send('Checking for updates...')
            await self.check_for_updates()

        await msg.add_reaction('\N{WHITE HEAVY CHECK MARK}')

    @commands.command(name='pdb', hidden=True, enabled=DEBUG)
    async def pdb(self, ctx):
        """Drop the process running the bot into pdb"""
        breakpoint()
        logger.info('Entered pdb')

    @tasks.loop(minutes=bot_check_interval)
    async def check_for_updates(self):
        """Check for and notify about playlist updates once every 20 minutes."""
        logger.info('Checking for updates to "%s"', self.pl_name)
        playlist = self._get_playlist()
        # FIXME: This doesn't report new/removed tracks on initialization because I can't get a
        #        specific snapshot of a playlist. There's currently no serialization or storage of
        #        Tracks across program runs, so I have no way to relay the differences.
        if playlist.snapshot_id != self.snap_id:
            # Snapshot ids differ. Need to send updates and then save the new pl
            logger.info('check_for_updates: snapshot ids differ')

            removed_tracks, new_tracks = self.pl.get_differences(playlist)
            for track in removed_tracks:
                await self.update_channel.send(embed=self._embed_from_track(track, new=False))

            for track in new_tracks:
                await self.update_channel.send(embed=self._embed_from_track(track))

            self._set_playlist(playlist)
            self._update_snapshot_id()

    @check_for_updates.before_loop
    async def before_bot_ready(self):
        # Need to wait until the bot is running to get the Channel info
        await self.bot.wait_until_ready()
        self.update_channel = [c for c in self.bot.get_all_channels()
                               if c.name == config('HOOK_UPDATE_CHANNEL')][0]


@bot.event
async def on_ready():
    status = '\N{musical note}\N{page with curl}'
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching,
                                                        name=status))


bot.add_cog(HookBot(bot))
# TODO: settings() function to return the most important class attributes, + something to Embed them
logger.info('Starting bot with prefix "%s" and check interval %f', bot_prefix, bot_check_interval)
bot.run(bot_token)
