"""Bounded, thread-safe audio queues and stateful PCM conversion; no devices."""
import audioop
from collections import deque
import threading
import time

DISCORD_FRAME = 3840  # 20 ms, 48 kHz, stereo, signed 16-bit LE
LIVE_FRAME = 960      # 20 ms, 24 kHz, mono, signed 16-bit LE


class InputBuffer:
    def __init__(self, max_frames=10, clock=time.monotonic):
        self.lock = threading.Lock()
        self.frames = deque()
        self.max_frames = max_frames
        self.clock = clock
        self.epoch = 0
        self.enabled = False
        self.dropped = 0

    def enable(self, enabled):
        with self.lock:
            self.epoch += 1
            self.enabled = enabled
            self.frames.clear()

    def put(self, pcm, epoch):
        if len(pcm) != DISCORD_FRAME:
            return
        with self.lock:
            if not self.enabled or epoch != self.epoch:
                return
            if len(self.frames) >= self.max_frames:
                self.frames.popleft()
                self.dropped += 1
            self.frames.append((self.clock(), pcm))

    def take(self):
        with self.lock:
            now = self.clock()
            while self.frames and now - self.frames[0][0] > 0.20:
                self.frames.popleft()
                self.dropped += 1
            return self.frames.popleft()[1] if self.enabled and self.frames else bytes(DISCORD_FRAME)


class Converter:
    def __init__(self):
        self.input_state = None
        self.output_state = None

    def to_live(self, pcm):
        if len(pcm) % 4:
            raise ValueError("Invalid stereo PCM frame")
        mono = audioop.tomono(pcm, 2, 0.5, 0.5)
        result, self.input_state = audioop.ratecv(mono, 2, 1, 48000, 24000, self.input_state)
        return result

    def to_discord(self, pcm):
        if len(pcm) % 2:
            raise ValueError("Invalid mono PCM frame")
        mono, self.output_state = audioop.ratecv(pcm, 2, 1, 24000, 48000, self.output_state)
        return audioop.tostereo(mono, 2, 1, 1)


class SpeechGate:
    """Simple energy detector, not a wake word or authoritative speech recognition."""
    def __init__(self, threshold=500, hangover=0.25):
        self.threshold = threshold
        self.hangover = hangover
        self.hot_frames = 0
        self.speaking = False
        self.last_hot = float("-inf")

    def update(self, pcm, now):
        hot = audioop.rms(pcm, 2) >= self.threshold
        self.hot_frames = self.hot_frames + 1 if hot else 0
        onset = False
        if hot:
            self.last_hot = now
            if self.hot_frames >= 2 and not self.speaking:
                self.speaking = True
                onset = True
        elif self.speaking and now - self.last_hot >= self.hangover:
            self.speaking = False
        return onset


class OutputBuffer:
    def __init__(self, max_frames=15, clock=time.monotonic):
        self.lock = threading.Lock()
        self.frames = deque()
        self.pending = b""
        self.converter = Converter()
        self.max_frames = max_frames
        self.clock = clock
        self.generation = 0
        self.blocked = True
        self.closed = False
        self.first_queued = None
        self.first_read = None
        self.markers = deque(maxlen=200)
        self.mark_queue = True
        self.mark_read = True

    def invalidate(self, blocked=True):
        with self.lock:
            self.generation += 1
            self.blocked = blocked
            self.frames.clear()
            self.pending = b""
            self.converter = Converter()
            self.mark_queue = True
            self.mark_read = True

    def permit(self):
        with self.lock:
            if not self.closed:
                self.blocked = False

    def append(self, pcm, generation):
        with self.lock:
            if self.closed or self.blocked or generation != self.generation:
                return
            combined = self.pending + self.converter.to_discord(pcm)
            count = len(combined) // DISCORD_FRAME
            if len(self.frames) + count > self.max_frames:
                raise BufferError("Playback overflow")
            for i in range(count):
                frame = combined[i * DISCORD_FRAME:(i + 1) * DISCORD_FRAME]
                self.frames.append(frame)
                if self.mark_queue and audioop.rms(frame, 2) > 30:
                    now = self.clock()
                    if self.first_queued is None:
                        self.first_queued = now
                    self.markers.append({"stage": "first_queued_not_audible", "monotonic": now})
                    self.mark_queue = False
            self.pending = combined[count * DISCORD_FRAME:]

    def read(self):
        with self.lock:
            if self.closed:
                return b""
            if self.blocked or not self.frames:
                return bytes(DISCORD_FRAME)
            frame = self.frames.popleft()
            if self.mark_read and audioop.rms(frame, 2) > 30:
                now = self.clock()
                if self.first_read is None:
                    self.first_read = now
                self.markers.append({"stage": "first_source_read_not_audible", "monotonic": now})
                self.mark_read = False
            return frame

    def close(self):
        self.invalidate()
        with self.lock:
            self.closed = True
