"""Plugin host adapters for PluginProof.

Everything here satisfies the :class:`pluginproof.contract.PluginHost` Protocol:
an object with a ``name: str`` attribute and a
``render(input_signal, samplerate) -> np.ndarray`` method.

I/O convention (uniform across all hosts): **float32 mono** ``(samples,)`` in,
**float32 mono** ``(samples,)`` out, same length as the input.

Adapters
--------
- :class:`PedalboardHost` — loads a real VST3/AU via Spotify's ``pedalboard``.
  ``pedalboard`` is imported lazily inside the class so the rest of the suite
  works without it installed.
- :class:`WavFileHost` — offline fallback: ``render`` **ignores the input
  signal** and returns audio read from a WAV file that was processed
  externally (e.g. in a DAW). Use :func:`write_input_wav` to export the test
  signal for external processing first.
- :func:`resolve_host` — maps a plugin spec string to the right adapter.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import numpy as np

from pluginproof.fixtures.biquad import BiquadFixture, PureGainFixture

__all__ = ["PedalboardHost", "WavFileHost", "resolve_host", "write_input_wav"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _to_float32_mono(data: np.ndarray) -> np.ndarray:
    """Convert wav data of any dtype/channel layout to float32 mono (samples,)."""
    x = np.asarray(data)
    # Downmix multichannel (scipy returns (samples, channels)) to mono.
    if x.ndim == 2:
        x = x.mean(axis=1)
    x = x.reshape(-1)
    if x.dtype == np.int16:
        x = x.astype(np.float32) / 32768.0
    elif x.dtype == np.int32:
        x = x.astype(np.float32) / 2147483648.0
    elif x.dtype == np.uint8:
        x = (x.astype(np.float32) - 128.0) / 128.0
    else:
        x = x.astype(np.float32)
    return x


def _match_length(y: np.ndarray, n: int) -> np.ndarray:
    """Trim or zero-pad y to exactly n samples (contract: output length == input length)."""
    if len(y) > n:
        return y[:n]
    if len(y) < n:
        return np.pad(y, (0, n - len(y)))
    return y


def write_input_wav(signal: np.ndarray, sr: int, path: str | Path) -> Path:
    """Write a float32 mono test signal to a WAV file.

    Companion to :class:`WavFileHost`: export the harness's test signal so the
    user can process it externally (in a DAW / standalone plugin host), then
    point ``WavFileHost(in_wav, out_wav)`` at the rendered result.
    """
    from scipy.io import wavfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), int(sr), np.asarray(signal, dtype=np.float32).reshape(-1))
    return path


# ---------------------------------------------------------------------------
# PedalboardHost
# ---------------------------------------------------------------------------

class PedalboardHost:
    """Hosts a real VST3/AU plugin via Spotify's ``pedalboard``.

    ``pedalboard`` is imported lazily (only when this class is instantiated),
    so the rest of PluginProof works on machines without it.

    Mono/stereo adaptation: the mono input is duplicated to a (2, samples)
    stereo buffer before processing (many VST3s are stereo-only), and the
    plugin's output is averaged back down to mono, then trimmed/padded to the
    input length.
    """

    def __init__(self, path: str | Path, **plugin_kwargs) -> None:
        try:
            import pedalboard  # lazy: optional dependency
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "PedalboardHost requires the 'pedalboard' package "
                "(pip install pedalboard). It is optional for the rest of "
                "PluginProof; use 'fixture:biquad' or WavFileHost instead."
            ) from exc

        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"Plugin file not found: {self.path}")
        self._plugin = pedalboard.load_plugin(str(self.path), **plugin_kwargs)
        self.name = self.path.name

    # -- parameter API ---------------------------------------------------------
    @property
    def parameters(self) -> dict[str, object]:
        """Dict of parameter name -> current value for the loaded plugin."""
        return {
            pname: getattr(self._plugin, pname)
            for pname in self._plugin.parameters.keys()
        }

    def set_param(self, name: str, value) -> None:
        """Set a plugin parameter by name. Raises KeyError for unknown names."""
        if name not in self._plugin.parameters:
            raise KeyError(
                f"Plugin {self.name!r} has no parameter {name!r}; "
                f"available: {sorted(self._plugin.parameters.keys())}"
            )
        setattr(self._plugin, name, value)

    # -- PluginHost Protocol ---------------------------------------------------
    def render(self, input_signal: np.ndarray, samplerate: int) -> np.ndarray:
        x = np.asarray(input_signal, dtype=np.float32).reshape(-1)
        stereo = np.vstack([x, x])  # (2, samples): safest for stereo-only VST3s
        out = self._plugin(stereo, sample_rate=float(samplerate), reset=True)
        out = np.asarray(out, dtype=np.float32)
        if out.ndim == 2:
            # pedalboard returns (channels, samples) when given (channels, samples)
            mono = out.mean(axis=0)
        else:
            mono = out
        return _match_length(mono, len(x)).astype(np.float32)


# ---------------------------------------------------------------------------
# WavFileHost
# ---------------------------------------------------------------------------

class WavFileHost:
    """Fallback adapter for plugins we cannot host in-process.

    **Important:** :meth:`render` IGNORES the ``input_signal`` argument and
    simply returns the audio stored in ``out_wav`` (converted to float32 mono,
    resampled to the requested samplerate if the file's rate differs, and
    trimmed/zero-padded to the input signal's length to honor the contract).

    Intended workflow:

    1. Generate the test signal and save it with :func:`write_input_wav`
       (or :meth:`WavFileHost.write_input_wav`) to ``in_wav``.
    2. Process ``in_wav`` through the plugin externally (DAW, standalone
       host, hardware loopback...) and save the result as ``out_wav``.
    3. Run the harness with this host; measurements compare the harness's
       input signal against the externally rendered ``out_wav``.

    The comparison is only meaningful if ``out_wav`` really is ``in_wav``
    processed by the device under test — the harness cannot verify that.
    """

    def __init__(self, in_wav: str | Path, out_wav: str | Path) -> None:
        self.in_wav = Path(in_wav)
        self.out_wav = Path(out_wav)
        self.name = f"wavfile:{self.out_wav.name}"

    def write_input_wav(self, signal: np.ndarray, sr: int) -> Path:
        """Write the test signal to this host's ``in_wav`` for external processing."""
        return write_input_wav(signal, sr, self.in_wav)

    def render(self, input_signal: np.ndarray, samplerate: int) -> np.ndarray:
        from scipy.io import wavfile
        from scipy.signal import resample_poly

        if not self.out_wav.exists():
            raise FileNotFoundError(
                f"WavFileHost: rendered output file not found: {self.out_wav}. "
                f"Process {self.in_wav} through your plugin externally and save "
                f"the result there first."
            )
        file_sr, data = wavfile.read(str(self.out_wav))
        y = _to_float32_mono(data)
        if int(file_sr) != int(samplerate):
            ratio = Fraction(int(samplerate), int(file_sr)).limit_denominator(1000)
            y = resample_poly(y.astype(np.float64), ratio.numerator, ratio.denominator)
            y = y.astype(np.float32)
        n = int(np.asarray(input_signal).reshape(-1).shape[0])
        return _match_length(y, n).astype(np.float32)


# ---------------------------------------------------------------------------
# resolve_host
# ---------------------------------------------------------------------------

_FIXTURES = {
    "biquad": BiquadFixture,
    "gain": PureGainFixture,
    "puregain": PureGainFixture,
}


def resolve_host(spec: str, **kwargs):
    """Map a plugin spec string to a PluginHost implementation.

    - ``"fixture:biquad"`` -> :class:`BiquadFixture` (kwargs forwarded, e.g.
      ``cutoff_hz=500, buggy=True``); ``"fixture:gain"`` -> :class:`PureGainFixture`.
    - a path ending in ``.vst3`` (or ``.component`` for AU) -> :class:`PedalboardHost`.
    - ``"wav"`` with ``in_wav=...``/``out_wav=...`` kwargs -> :class:`WavFileHost`.

    Raises ``ValueError`` with a clear message for anything else.
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError(
            f"Plugin spec must be a non-empty string, got {spec!r}. "
            "Try 'fixture:biquad' or a path to a .vst3 file."
        )
    spec = spec.strip()

    if spec.startswith("fixture:"):
        fixture_name = spec.split(":", 1)[1].lower()
        cls = _FIXTURES.get(fixture_name)
        if cls is None:
            raise ValueError(
                f"Unknown fixture {fixture_name!r} in spec {spec!r}; "
                f"available fixtures: {sorted(set(_FIXTURES))}"
            )
        return cls(**kwargs)

    lower = spec.lower()
    if lower.endswith((".vst3", ".component")):
        return PedalboardHost(spec, **kwargs)

    if lower in ("wav", "wavfile"):
        try:
            return WavFileHost(kwargs.pop("in_wav"), kwargs.pop("out_wav"))
        except KeyError as exc:
            raise ValueError(
                "WavFileHost spec 'wav' requires in_wav=... and out_wav=... kwargs"
            ) from exc

    raise ValueError(
        f"Cannot resolve plugin spec {spec!r}. Expected one of: "
        "'fixture:biquad' / 'fixture:gain', a path ending in .vst3 (or "
        ".component), or 'wav' with in_wav/out_wav kwargs."
    )
