from types import SimpleNamespace
import threading

from panam_dj.catalog import DJError, Track


class FakeProvider:
    async def resolve(self, query):
        if query == "missing":
            raise DJError("Unknown catalog ID.")
        return Track(query, query)


class FakeSource:
    def __init__(self, track):
        self.track = track
        self.cleanups = 0

    def cleanup(self):
        self.cleanups += 1


class FakeVoice:
    """Supported VoiceClient surface, with explicit control over callback timing."""

    def __init__(self, guild_id, channel_id):
        self.guild = SimpleNamespace(id=guild_id)
        self.channel = SimpleNamespace(id=channel_id)
        self.connected = True
        self.paused = False
        self.current = None
        self.after = None
        self.history = []
        self.stopped_callbacks = []
        self.fail_play = set()
        self.fail_disconnect = False
        self.disconnects = 0

    def is_connected(self):
        return self.connected

    def play(self, source, *, after):
        if source.track.key in self.fail_play:
            raise RuntimeError("synthetic voice failure")
        if self.current is not None or not self.connected:
            raise RuntimeError("Invalid fake voice state")
        self.current, self.after = source, after
        self.paused = False
        self.history.append(source.track.key)

    def finish(self, error=None, *, threaded=False):
        source, callback = self.current, self.after
        self.current = self.after = None

        def complete():
            try:
                callback(error)
            finally:
                source.cleanup()

        if threaded:
            thread = threading.Thread(target=complete)
            thread.start()
            thread.join()
        else:
            complete()
        return callback

    def stop(self):
        if self.current is not None:
            self.stopped_callbacks.append(self.after)
            self.current.cleanup()
            self.current = self.after = None
        self.paused = False

    def fire_stopped(self):
        callbacks, self.stopped_callbacks = self.stopped_callbacks, []
        for callback in callbacks:
            callback(None)

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    async def disconnect(self, *, force=False):
        self.disconnects += 1
        if self.fail_disconnect:
            raise RuntimeError("synthetic disconnect failure")
        self.stop()
        self.connected = False
