"""Pulls a sung take onto the original singer's notes: auto-tune toward one specific melody.

The take is lined up and pitch-tracked as in pitch.py. At every moment the gap to the
original melody (in the take's own key and octave) becomes a correction. The WORLD vocoder
then re-synthesizes the take with its pitch moved by that correction, keeping the singer's
own voice and timing. Strength 1.0 lands exactly on the original's pitch, including its
vibrato and slides; 0.5 goes halfway.

Where the original isn't singing but the take is, the nearest correction is held for up to
HOLD seconds and then fades out, so held notes and early entries don't jump.

  python tune.py song.mp3 take.m4a out_dir/ [--strength 0.8] [--take-has-music]
"""

import json
import sys
from pathlib import Path

import librosa
import numpy as np
import pyworld
import soundfile as sf
from scipy.ndimage import median_filter, uniform_filter1d

sys.path.insert(0, str(Path(__file__).resolve().parent))  # find sibling modules even under python -I
import pitch  # noqa: E402
import sync  # noqa: E402

FRAME_MS = 5.0
HOLD = 0.25        # seconds a correction is carried past the end of the original's note
MAX_FIX = 7        # semitones; bigger gaps are tracking errors, not wrong notes, so leave them


def corrections(tracks):
    """Semitones to add to the take at each pitch frame (0 where there is nothing to fix)."""
    t, diff = tracks["t"], tracks["diff"]
    fix = np.where(np.isnan(diff), np.nan, -diff / 100)
    fix[np.abs(fix) > MAX_FIX] = np.nan
    # Hold the nearest known correction across short gaps, then fall back to none.
    frame = t[1] - t[0]
    hold = int(HOLD / frame)
    known = ~np.isnan(fix)
    idx = np.where(known, np.arange(len(fix)), -10 ** 9)
    last = np.maximum.accumulate(idx)
    nxt = np.where(known, np.arange(len(fix)), 10 ** 9)
    nxt = np.minimum.accumulate(nxt[::-1])[::-1]
    out = np.zeros(len(fix))
    for i in range(len(fix)):
        if known[i]:
            out[i] = fix[i]
        else:
            near = min((i - last[i], last[i]), (nxt[i] - i, nxt[i]))
            if near[0] <= hold:
                out[i] = fix[near[1]]
    # Smooth: drop single-frame tracker glitches, then soften the steps (about 50 ms).
    out = median_filter(out, size=5)
    return uniform_filter1d(out, size=3)


def tune(song_path, take_path, out_dir, strength=0.8, take_has_music=False):
    out_dir = Path(out_dir)
    score, tracks = pitch.analyze(song_path, take_path, out_dir, take_has_music)
    fix = corrections(tracks) * strength

    y, sr = sf.read(out_dir / "synced_vocal.wav", dtype="float64")
    if y.ndim > 1:
        y = y.mean(axis=1)
    f0, times = pyworld.harvest(y, sr, f0_floor=60, f0_ceil=1100, frame_period=FRAME_MS)
    f0 = pyworld.stonemask(y, f0, times, sr)
    sp = pyworld.cheaptrick(y, f0, times, sr)
    ap = pyworld.d4c(y, f0, times, sr)
    shift = np.interp(times, tracks["t"], fix)
    tuned = pyworld.synthesize(f0 * 2 ** (shift / 12), sp, ap, sr, FRAME_MS)[:len(y)]
    # WORLD fills silence with faint noise; keep the original's silences silent.
    tuned[np.abs(y) == 0] = 0
    sf.write(out_dir / "tuned_vocal.wav", tuned.astype(np.float32), sr)

    song_vocal, instrumental = sync.separate(song_path, out_dir / "work" / "song")
    inst, _ = librosa.load(instrumental, sr=sr, mono=False)
    sync.write_mix(tuned.astype(np.float32), np.atleast_2d(inst), song_vocal, out_dir / "tuned_mix.wav")

    # Score the result the same way the take was scored.
    after = pitch.track(out_dir / "tuned_vocal.wav")
    n = min(len(after), len(tracks["target"]))
    both = ~np.isnan(after[:n]) & ~np.isnan(tracks["target"][:n])
    after_diff = pitch.fold((after[:n][both] - tracks["target"][:n][both]) * 100)
    moved = np.abs(shift[f0 > 0])
    result = {"before": score["on_pitch"], "after": round(float(np.mean(np.abs(after_diff) < pitch.ON_PITCH)), 3),
              "strength": strength,
              "typical_fix_cents": round(float(np.median(moved)) * 100) if moved.size else 0,
              "key_shift": score["key_shift"], "octave": score["octave"]}
    (out_dir / "tune.json").write_text(json.dumps(result))
    return result


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    strength = float(sys.argv[sys.argv.index("--strength") + 1]) if "--strength" in sys.argv else 0.8
    args = [a for a in args if a != str(sys.argv[sys.argv.index("--strength") + 1])] if "--strength" in sys.argv else args
    if len(args) != 3:
        sys.exit(__doc__)
    print(json.dumps(tune(*args, strength=strength, take_has_music="--take-has-music" in sys.argv), indent=1))
