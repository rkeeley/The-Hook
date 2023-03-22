from __future__ import annotations

import random

from datetime import datetime
from os.path import exists as path_exists
from typing import List

import discord

from decouple import config, UndefinedValueError
from discord.ext import commands, tasks

import hook_logging

from mongo_client import HookMongoClient
from playlist import Playlist
from spotipy_client import SpotipyClient
from track import Track

DEBUG = config('HOOK_DEBUG', default=False, cast=bool)
REPORT_REMOVALS = config('HOOK_REPORT_REMOVALS', default=False, cast=bool)

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

    def __init__(self, bot, check_interval: float = None, spotipy_client: SpotipyClient = None):
        self.bot = bot
        self.pl_name: str = config('HOOK_PLAYLIST_NAME', cast=str)
        self.pl: Playlist = None
        self.update_channel = None
        self.snap_id: str = ''
        self.logger = hook_logging._init_logger(__name__)
        self.sp = spotipy_client or SpotipyClient()
        self.db_client = HookMongoClient()

        if not self._set_playlist():
            self.logger.critical('Failed to get the "%s" playlist from Spotify.', self.pl_name)
            raise Exception('Failed to get playlist from Spotify')

        self._update_snapshot_id()

        self.check_for_updates.change_interval(minutes=float(check_interval or config('HOOK_CHECK_INTERVAL', cast=float)))
        self.check_for_updates.start()

    @commands.Cog.listener()
    async def on_ready(self):
        status = '\N{musical note}\N{page with curl}'
        await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching,
                                                            name=status))


    def _get_playlist(self) -> Playlist:
        """Get a Playlist object using self.pl.id if available and self.pl_name otherwise."""
        try:
            if self.pl and self.pl.id:
                playlist = Playlist.from_id(self.sp, self.pl.id)
            else:
                playlist = Playlist.from_name(self.sp, self.pl_name)
        except KeyError:
            self.logger.critical('_get_playlist: Failed to get playlist from Spotify')
            return None

        return playlist

    def _set_playlist(self, playlist: Playlist = None) -> bool:
        """Set self.pl to the input playlist or get the configured one from Spotify, updating the database as needed

        :returns: True if successful, False otherwise
        :rtype bool:
        """
        if not playlist:
            playlist = self._get_playlist()
            if not playlist:
                self.logger.critical('_set_playlist: Failed to get playlist from Spotify')
                return False # TODO: raise?

        self.pl = playlist
        for track in self.pl.tracks:
            self.db_client.save_track(track)
        return True

    def _update_snapshot_id(self) -> bool:
        """Compare :attr snap_id: to the snapshot id of :attr pl: and update the former if needed.

        self.pl must be set before this method is called.

        This does not affect the :attr pl: attribute or any attribute besides :attr snap_id:.

        :returns: True if the snapshot id is new; False otherwise
        :rtype bool:
        """
        if not self.snap_id or self.snap_id != self.pl.snapshot_id:
            self.logger.info('_update_snapshot_id: updating snapshot id')
            self.db_client.save_snapshot_id(self.pl.snapshot_id)
            self.snap_id = self.pl.snapshot_id
            return True

        return False

    async def _remove_track(self, track: Track):
        """Remove a track from the database, reporting if configured to do so"""
        self.db_client.remove_track(track)

        if REPORT_REMOVALS:
            await self.update_channel.send(embed=self._embed_from_track(track, new=False))

    def _embed_from_track(self, track: Track, new=True, pl_name=None) \
    -> discord.embeds.Embed:
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
        genres = self.sp.client.artist(track.artists[0]['id'])['genres']
        pl_name = pl_name or self.pl_name

        embed = discord.embeds.Embed(
            title=track.name,
            type='rich',
            description=f'{artists} • *{track.album["name"]}*',
            url=track.album['external_urls']['spotify'],
            timestamp=track.raw['added_at'],  # FIXME: conditional based on what's in the data
            color=color,
        ).set_thumbnail(
            url=track.album['images'][0]['url'],
        ).set_author(
            # TODO: add "by {user}" (if new?) in case there's no pfp or it's not obvious who did it
            name=f'Song {"added to" if new else "removed from"} "{pl_name}"',
            url=track.raw['track']['external_urls']['spotify'],
            icon_url=(discord.Embed.Empty if not new
                      else self.sp.client.user(track.raw['added_by']['id'])['images'][0]['url']),
        )

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
        it starts at 0 and ends at (playlist length) - 1. If the input is not between
        (-playlist_length) and (playlist_length - 1), or if nothing was input, the default of the
        most recent playlist addition will be sent. Nothing will be posted if anything other than a
        number is input.
        """,
    )
    async def embed_track(self, ctx, track_offset: int =-1):
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
        await ctx.send(self.pl.data['external_urls']['spotify'])

    @commands.command(
        name='check',
        aliases=['c'],
        brief='Check the watched playlist for updates',
        help=f"""Check the watched playlist for updates.

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
        name='set_check_interval',
        brief='Set the time for which the bot will wait to check for playlist updates',
    )
    async def set_check_interval(self, ctx, check_interval: float):
        if not check_interval:
            await ctx.send('Must provide a check_interval value')
            return

        old_check_interval = self.check_for_updates.minutes
        self.check_for_updates.change_interval(minutes=float(check_interval))
        await ctx.send(f'Updated the check interval from {old_check_interval} to {self.check_for_updates.minutes}')

    @commands.command(
        name='set_prefix',
        brief='Set the prefix for bot commands',
    )
    async def set_prefix(self, ctx, prefix: str):
        if not prefix:
            await ctx.send('Must provide a prefix')
            return

        old_prefix = self.bot.command_prefix
        self.bot.command_prefix = prefix
        await ctx.send(f'Updated the bot prefix from {old_prefix} to {self.bot.command_prefix}')

    @commands.command(
        name='pdb',
        hidden=True,
        enabled=DEBUG,
        help='Drop the program into a pdb shell. This should only be enabled in debug deployments!',
    )
    async def pdb(self, ctx):
        """Drop the process running the bot into pdb"""
        breakpoint()
        self.logger.info('Entered pdb')

    @tasks.loop()
    async def check_for_updates(self):
        """Check for and notify about playlist updates"""
        self.logger.info('Checking for updates to "%s"', self.pl_name)
        playlist = self._get_playlist()
        # Note that this doesn't report changes made to the playlist while the bot wasn't running.
        if playlist.snapshot_id != self.snap_id:
            # Snapshot ids differ. Need to send updates and then save the new pl
            self.logger.info('check_for_updates: snapshot ids differ')

            removed_tracks, new_tracks = self.pl.get_differences(playlist)
            for track in removed_tracks:
                self._remove_track(track)

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


def initialize_bot(prefix: str, check_interval: float = None,
                   spotipy_client: SpotipyClient = None):
    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(intents=intents, command_prefix=prefix)
    bot.add_cog(HookBot(bot, check_interval=check_interval, spotipy_client=spotipy_client))

    return bot
