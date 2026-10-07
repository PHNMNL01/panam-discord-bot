"""Local raw PCM adapters. Importing this module opens no devices.

48 kHz PCM16 LE only: mono/stereo input, stereo output, 3,840-byte
canonical frames. Callback staging holds less than one 20 ms frame.
"""
import audioop
import sys
import threading

from .audio import DISCORD_FRAME
from .config import SafeError

RATE = 48000
SAMPLES = 960
MAX_CALLBACK_SAMPLES = SAMPLES * 10  # Bound each callback to at most 200 ms.


def get_backend(backend=None):
    if backend is not None:
        return backend
    if sys.platform != "win32" or sys.byteorder != "little":
        raise SafeError("Local audio requires little-endian Windows.")
    import sounddevice
    return sounddevice


def devices(backend=None):
    """Passive enumeration only; indices belong to this PortAudio inventory."""
    sd = get_backend(backend)
    try:
        fields = ("index", "name", "hostapi", "max_input_channels",
                  "max_output_channels", "default_samplerate")
        return {"default_input": int(sd.default.device[0]),
                "default_output": int(sd.default.device[1]),
                "devices": [{key: device[key] for key in fields} for device in sd.query_devices()]}
    except Exception:
        raise SafeError("Audio device enumeration failed.") from None


def validate_device(sd, device, channels, kind):
    if channels not in ((1, 2) if kind == "input" else (2,)):
        raise SafeError("Use mono/stereo input and stereo output only.")
    try:
        if device is None:
            device = int(sd.default.device[0 if kind == "input" else 1])
        if type(device) is not int or device < 0:
            raise ValueError
        info = sd.query_devices(device)
        if info["max_" + kind + "_channels"] < channels:
            raise ValueError
        check = sd.check_input_settings if kind == "input" else sd.check_output_settings
        check(device=device, channels=channels, dtype="int16", samplerate=RATE)
        return device
    except Exception:
        raise SafeError("Selected " + kind + " device cannot provide 48000 Hz PCM16 with the requested channels.") from None


class RawAdapter:
    """Own one physical stream; retain ownership if close cannot be confirmed."""
    def __init__(self, backend, device):
        self.backend = backend
        self.device = device
        self.stream = None
        self.lock = threading.Lock()
        self.pending = b""
        self.failed = threading.Event()
        self.cleanup_failed = False

    @property
    def active(self):
        return self.stream is not None and self.stream.active

    def close(self):
        if self.cleanup_failed:
            raise SafeError("Local audio stream closure unconfirmed; no automatic retry.")
        with self.lock:
            self.pending = b""
        if self.stream is None:
            return
        # Close is still attempted when abort fails. Never lose an unclosed stream.
        try:
            self.stream.abort()
        except Exception:
            pass
        try:
            self.stream.close()
        except Exception:
            self.failed.set()
            self.cleanup_failed = True
            raise SafeError("Local audio stream closure unconfirmed.") from None
        self.stream = None


class Microphone(RawAdapter):
    def __init__(self, buffer, *, backend=None, device=None, channels=1):
        super().__init__(backend, device)
        self.buffer = buffer
        self.channels = channels
        self.forwarding = False
        self.muted = False
        self.epoch = None

    def start(self):
        if self.stream is not None:
            raise SafeError("Microphone stream already owned.")
        sd = get_backend(self.backend)
        self.device = validate_device(sd, self.device, self.channels, "input")
        try:
            self.stream = sd.RawInputStream(device=self.device, samplerate=RATE,
                                            channels=self.channels, dtype="int16",
                                            blocksize=SAMPLES, callback=self.callback)
            self.stream.start()
        except Exception:
            self.close()
            raise SafeError("Microphone stream could not start.") from None

    def set_forwarding(self, enabled):
        with self.lock:
            self.forwarding = enabled
            self.pending = b""
            self.buffer.enable(enabled and not self.muted)

    def set_muted(self, muted):
        with self.lock:
            self.muted = muted
            self.pending = b""
            self.buffer.enable(self.forwarding and not muted)

    def callback(self, indata, frames, _time, status):
        with self.lock:
            try:
                if status or not 0 < frames <= MAX_CALLBACK_SAMPLES or len(indata) != frames * self.channels * 2:
                    raise ValueError
                # No conversion or retention while listening without a call/muted.
                if not self.forwarding or self.muted or not self.buffer.enabled:
                    self.pending = b""
                    return
                epoch = self.buffer.epoch
                if self.epoch != epoch:
                    self.pending = b""
                    self.epoch = epoch
                pcm = bytes(indata)
                if self.channels == 1:
                    pcm = audioop.tostereo(pcm, 2, 1, 1)
                combined = self.pending + pcm
                complete = len(combined) // DISCORD_FRAME * DISCORD_FRAME
                for offset in range(0, complete, DISCORD_FRAME):
                    self.buffer.put(combined[offset:offset + DISCORD_FRAME], epoch)
                self.pending = combined[complete:]
            except Exception:
                self.pending = b""
                self.forwarding = False
                self.buffer.enable(False)
                self.failed.set()

    def close(self):
        self.set_forwarding(False)
        super().close()


class Playback(RawAdapter):
    def __init__(self, output, *, backend=None, device=None):
        super().__init__(backend, device)
        self.output = output  # Borrowed; LiveSession owns this OutputBuffer.
        self.generation = None

    def start(self):
        if self.stream is not None:
            raise SafeError("Playback stream already owned.")
        sd = get_backend(self.backend)
        self.backend = sd
        self.device = validate_device(sd, self.device, 2, "output")
        try:
            self.stream = sd.RawOutputStream(device=self.device, samplerate=RATE,
                                             channels=2, dtype="int16", blocksize=SAMPLES,
                                             callback=self.callback)
            self.stream.start()
        except Exception:
            self.close()
            raise SafeError("Playback stream could not start.") from None

    def callback(self, outdata, frames, _time, status):
        # Reject oversized/malformed requests before allocating callback storage.
        if not 0 < frames <= MAX_CALLBACK_SAMPLES or len(outdata) != frames * 4:
            self.failed.set()
            raise self.backend.CallbackAbort
        with self.lock:
            outdata[:] = bytes(len(outdata))
            if self.failed.is_set():
                return
            try:
                if status:
                    raise ValueError
                offset = 0
                while offset < len(outdata):
                    generation = self.output.generation
                    if generation != self.generation or self.output.blocked or self.output.closed:
                        self.pending = b""
                        self.generation = generation
                    if self.output.blocked or self.output.closed:
                        return
                    if not self.pending:
                        self.pending = self.output.read()
                        if not self.pending:
                            return
                        if len(self.pending) != DISCORD_FRAME:
                            raise ValueError
                    count = min(len(self.pending), len(outdata) - offset)
                    outdata[offset:offset + count] = self.pending[:count]
                    self.pending = self.pending[count:]
                    offset += count
            except Exception:
                self.pending = b""
                outdata[:] = bytes(len(outdata))
                self.failed.set()
