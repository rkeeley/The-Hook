from __future__ import annotations

import datetime
import logging
import random

from logging.handlers import RotatingFileHandler
from os.path import exists as path_exists
from typing import List, Optional, Union

import discord
import spotipy
import spotify_objects as so

from decouple import config, UndefinedValueError
from discord.ext import commands, tasks
# FIXME: I think this causes datetime to be re-imported, shadowing the one above :\
#        Wildcard imports really can be dangerous. Welp
from spotify_objects import *
from spotipy.oauth2 import SpotifyOAuth


# Colors for Embeds: Spotify green, or some red-ish analogoue of its purple-ish tetradic color
DISC_COLOR_SPOTIFY_GREEN = discord.Color.from_rgb(30, 215, 96)
DISC_COLOR_SPOTIFY_RED = discord.Color.from_rgb(186, 30, 53)

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


def get_playlist_tracks(pl_id: str) -> List[Track]:
    """Return a list of all the Tracks in the playlist with id :param pl_id:

    :param pl_id: The 'id' attribute of the Playlist object
    :returns tracks: A List of Track objects in the Playlist
    """

    limit = 100
    offset = 0
    track_obj = sp.playlist_tracks(pl_id, limit=limit, offset=offset)
    tracks = [Track(**t) for t in track_obj['items']]

    while len(tracks) < track_obj['total']:
        offset += limit
        track_obj = sp.playlist_tracks(pl_id, limit=limit, offset=offset)
        tracks.extend([Track(**t) for t in track_obj['items']])

    return tracks


def artists_and_title_list(tracks: List[Track], limit: int = None) -> List[str]:
    """Return a list of strings corresponding to artist names and the track title

    :param tracks: List of Track objects
    :param limit: Maximum number of strings to return
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


def simplified_playlist_from_search(name: str) -> SimplifiedPlaylist:
    """Return the dict with the SimplifiedPlaylist data from a search endpoint response.

    Note that this is the SimplifiedPlaylist, not the full Playlist data with full Track
    information.
    """
    # FIXME: Need to get the right variables in here
    # FIXME: I'm not sure it's ever useful to have a SimplifiedPlaylist instead of a full Playlist
    playlist = sp.search(q=f'"{name}"', type='playlist', limit=1)
    if not playlist:
        logger.critical('Spotify search for playlist "%s" failed', name)
        raise KeyError(f'Spotify search for playlist "{name}" failed')

    try:
        playlist = playlist['playlists']['items'][0]
    except IndexError:
        logger.critical('No playlist data returned from Spotify for "%s"', name)
        raise KeyError(f'Could not find a playlist called "{name}".') from None

    return SimplifiedPlaylist(**playlist)


def playlist_from_search(name: str) -> Playlist:
    """Search for a playlist called :param name: and return it as a Playlist instance"""
    # FIXME: This would essentially be the same function as simplified_playlist_from_search. It
    #        feels weird to create the SimplifiedPlaylist object when I need to call sp.playlist
    #        anyway. Need to see which is easier on memory and runtime.
    simplified_pl = simplified_playlist_from_search(name)
    return playlist_from_id(simplified_pl.id)


def playlist_from_id(plid: str) -> Playlist:
    """Get the playlist with id :param plid: and return a Playlist object for it."""
    # FIXME: Need to get the right variables in here
    playlist = sp.playlist(plid)
    if not playlist:
        logger.critical('Spotify search for playlist with id %s failed', plid)
        raise KeyError(f'Could not find a playlist with id "{plid}"')

    return Playlist(**playlist)


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
                playlist = playlist_from_id(self.pl.id)
            else:
                playlist = playlist_from_search(self.pl_name)
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
            # FIXME: Is `not self.snap_id` really true when it's a new file? I don't remember
            #        setting that to None and the file existence isn't checked for here
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

    def _embed_from_track(self, pl_track: PlaylistTrack, new=True,
                          pl_name=None) -> discord.embeds.Embed:
        """Creates a formatted Embed object using :param track: data for a single Discord message.

        This function expects a PlaylistTrack because the embed information contains data about who
        added the song to the playlist, and when they did. It will not work with other Track-related
        classes.

        :param pl_track: PlaylistTrack object for the song to be embedded
        :param new: Whether this embed is for a newly-added Track or a removed one
            Embeds for added (new) and removed (not new) tracks contain different information.
        :return: a discord.embeds.Embed object to be sent in a discord Messageable
        """
        # TODO: There are two API calls in this function. See if they can be made redundant

        artists = ''.join([f'**{a["name"]}**, ' for a in pl_track.track.artists])[:-2]
        color = DISC_COLOR_SPOTIFY_GREEN if new else DISC_COLOR_SPOTIFY_RED
        genres = sp.artist(pl_track.track.artists[0]['id'])['genres']
        pl_name = pl_name or self.pl_name

        embed = discord.embeds.Embed(
            title=pl_track.track.name,
            type='rich',
            description=f'{artists} • *{pl_track.track.album["name"]}*',
            url=pl_track.track.album['external_urls']['spotify'],
            timestamp=datetime.datetime.fromisoformat(pl_track.added_at[:-1]),  # [:-1] for 'Z'ms
            color=color,
        ).set_thumbnail(
            url=pl_track.track.album['images'][0]['url'],
        ).set_author(
            # TODO: add "by {user}" (if new?) in case there's no pfp or it's not obvious who did it
            name=f'Song {"added to" if new else "removed from"} "{pl_name}"',
            url=pl_track.track.external_urls[0].spotify,
            icon_url=(discord.Embed.Empty if not new
                      else sp.user(pl_track.added_by.id)['images'][0]['url']),  # FIXME: *User class
        )

        # TODO: I only random.sample this in case there are like 10 genres listed. It would be nice
        #       to have them in the same order all the time when there are fewer than that.
        if new and genres:
            embed.add_field(
                name='Artist Genres',
                value=' • '.join(random.sample(genres, min(4, len(genres)))),
            )

        return embed

    @commands.command(
        name='embed',
        brief='Post the most recent playlist addition to the update channel',
        help="""Post the most recent playlist addition to the update channel.

        <track_offset> is the offset into the list, not the position of the track in the list, i.e.
        it starts at 0 and ends at (playlist length) - 1.

        If the input is not between (-playlist_length) and (playlist_length - 1), or if nothing
        was input, the default of the most recent playlist addition will be sent.

        Nothing will be posted if anything other than a number is input.
        """,
    )
    async def embed_track(self, ctx, track_offset: int = -1):
        if not self.pl.tracks:
            await ctx.send('The playlist is empty.')  # TODO: better message
            return

        if track_offset < -len(self.pl.tracks) or track_offset >= len(self.pl.tracks):
            track_offset = -1

        await ctx.send(embed=self._embed_from_track(self.pl.tracks[track_offset]))

    @commands.command(
        name='playlist',
        aliases=['pl'],
        brief='Send the watched playlist link to the update channel',
        help='Send the watched playlist link to the update channel.',
    )
    async def playlist(self, ctx):
        await ctx.send(self.pl.external_urls[0].spotify)

    @commands.command(
        name='check',
        aliases=['c'],
        brief='Check the watched playlist for updates',
        help=f"""Check the watched playlist for updates.

        Normally the bot checks for updates to the playlist every {bot_check_interval} minutes. This
        command tells it to check for updates immediately.

        The bot will send a message to the update channel before it starts its check. After the
        check is complete it will react to that message with a green and white checkmark emote.
        """,
    )
    async def check(self, ctx):
        async with ctx.typing():
            msg = await ctx.send('Checking for updates...')
            await self.check_for_updates()

        await msg.add_reaction('\N{WHITE HEAVY CHECK MARK}')

    @commands.command(
        name='pdb',
        hidden=True,
        enabled=DEBUG,
        help='Drop the program into a pdb shell. This should only be enabled in debug deployments!',
    )
    async def pdb(self, ctx):
        """Drop the process running the bot into pdb"""
        breakpoint()
        logger.info(f'Entered pdb for "{ctx.author.name}#{ctx.author.discriminator}".')

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
