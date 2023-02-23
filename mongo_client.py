from datetime import datetime

from decouple import config
from pymongo import MongoClient

from track import Track

DEFAULT_CONNECTION_STR = 'mongodb://127.0.0.1:27017'


class HookMongoClient(MongoClient):
    """Wrapper for pymongo.MongoClient"""
    def __init__(self, connection_str: str = None):
        if not connection_str:
            connection_str = config('HOOK_MONGODB_CONNECTION_STRING', default=DEFAULT_CONNECTION_STR, cast=str)

        super().__init__(connection_str)

    def save_track(self, track: Track):
        """Save :param track: to the database"""
        collection = self.the_hook.playlist
        # FIXME: Needs some translator for Track. raw isn't enough
        collection.insert_one({ track.id: track.raw })

    def find_track(self, track: Track):
        """Search the database for :param track: by name"""
        return self.find_track_by_id(track.id)

    def find_track_by_id(self, track_id: str):
        """Search the database for :param track: by id"""
        collection = self.the_hook.playlist
        return collection.find({ track_id: { '$exists': True }})

    def remove_track(self, track: Track):
        """Remove :param track: from the database"""
        for result in self.find_track(track):
            self.the_hook.playlist.delete_one({ '_id': result['_id'] })

    def all_tracks(self):
        """Return a list of all tracks stored in the database"""
        return [t for t in self.the_hook.playlist.find()]
