from datetime import datetime

from decouple import config
from pymongo import MongoClient
from pymongo import errors as MongoErrors

from track import Track

DEFAULT_CONNECTION_STR = 'mongodb://127.0.0.1:27017'


class HookMongoClient(MongoClient):
    SNAPSHOT_ID_KEY = 'snapshot_id'

    """Wrapper for pymongo.MongoClient"""
    def __init__(self, connection_str: str = None):
        if not connection_str:
            connection_str = config('HOOK_MONGODB_CONNECTION_STRING', default=DEFAULT_CONNECTION_STR, cast=str)

        super().__init__(connection_str)

    @property
    def collection(self):
        """Returns the mongodb Collection used for this playlist"""
        return self.the_hook.playlist # TODO: tie to individual users/Playlists

    def save_track(self, track: Track):
        """Save :param track: to the database"""
        # FIXME: Needs some translator for Track. raw isn't enough
        try:
            self.collection.insert_one({ '_id': track.id, 'track': track.raw })
        except MongoErrors.DuplicateKeyError:
            return

    def find_track(self, track: Track):
        """Search the database for :param track: by name"""
        return self.collection.find({ '_id': track.id })

    def remove_track(self, track: Track):
        """Remove :param track: from the database"""
        self.collection.delete_one({ '_id': track.id })

    def all_tracks(self):
        """Return a list of all tracks stored in the database"""
        return [track for track in self.collection.find() if 'track' in track.keys()]

    def save_snapshot_id(self, snapshot_id: str):
        """Save the input :param snapshot_id: to this collection"""
        try:
            self.collection.insert_one({ '_id': snapshot_id, self.SNAPSHOT_ID_KEY: True })
        except MongoErrors.DuplicateKeyError:
            # FIXME: this is ugly
            pass

    def _get_snapshot_id(self):
        """Returns the mongo object for the snapshot id document"""
        return self.collection.find_one({ self.SNAPSHOT_ID_KEY: { '$exists': True } })

    def get_snapshot_id(self) -> str:
        """Returns the snapshot id value for this collection

        :returns: [str] snapshot id if found; None otherwise"""
        # FIXME: idk how the python typing works here
        snapshot_id_obj = self._get_snapshot_id()
        if snapshot_id_obj:
            return snapshot_id_obj['_id']

        return None

    def update_snapshot_id(self, snapshot_id: str):
        """Upsert the collection's existing snapshot id entry with :param snapshot_id:"""
        # FIXME: I didn't test this much, but it seems to work as expected
        self.collection.update_one(
            { self.SNAPSHOT_ID_KEY: { "$exists": True } },
            { "_id": snapshot_id }
        )        