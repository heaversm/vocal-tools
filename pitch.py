"""How closely a sung take follows the original singer's melody.

The take is lined up with the song first (sync.py). Then the pitch of both vocals is
tracked every 23 ms (librosa's pYIN). Where both are singing, the gap between them is
measured in cents (100 cents = one semitone). If the take was in a different key, or an
octave away, the gap is measured against the melody moved into the take's key and octave,
so singing it lower isn't counted as wrong.

On pitch means within 50 cents: closer to the right note than to the one next to it.

  python pitch.py song.mp3 take.m4a out_dir/ [--take-has-music]
"""

import json
import sys
from pathlib import Path

import librosa
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))  # find sync.py even under python -I
import sync  # noqa: E402

SR = 22050
HOP = 512
FMIN, FMAX = librosa.note_to_hz("C2"), librosa.note_to_hz("C6")
ON_PITCH = 50  # cents


def track(path):
    """Pitch in MIDI note numbers per frame (NaN where nobody is singing)."""
    y, _ = librosa.load(path, sr=SR, mono=True)
    f0, voiced, _ = librosa.pyin(y, fmin=FMIN, fmax=FMAX, sr=SR, hop_length=HOP)
    midi = librosa.hz_to_midi(f0)
    midi[~voiced] = np.nan
    return midi


def fold(cents):
    """Into -600..600: an octave off counts as the same note."""
    return (cents + 600) % 1200 - 600


def pitch_match(song_path, take_path, out_dir, take_has_music=False):
    out_dir = Path(out_dir)
    report = sync.sync(song_path, take_path, out_dir, take_has_music)
    song_vocal, _ = sync.separate(song_path, out_dir / "work" / "song")  # already split by sync, so instant

    orig = track(song_vocal)
    yours = track(out_dir / "synced_vocal.wav")
    n = min(len(orig), len(yours))
    orig, yours = orig[:n], yours[:n]
    t = np.arange(n) * HOP / SR

    both = ~np.isnan(orig) & ~np.isnan(yours)
    if both.sum() < 20:
        raise RuntimeError("Not enough singing in both recordings at the same time to compare.")
    # Key: the whole-semitone shift that puts the most of the take on pitch. (Sync's own key
    # guess comes from a rough melody match; good enough to line up, not to score.)
    raw = (yours[both] - orig[both]) * 100
    key_shift = max(range(-6, 7), key=lambda k: np.mean(np.abs(fold(raw - 100 * k)) < ON_PITCH))
    target = orig + key_shift  # the melody moved into the take's key
    diff = np.full(n, np.nan)
    diff[both] = fold((yours[both] - target[both]) * 100)
    # Octave the take sat in relative to the original, for the chart.
    octave = int(np.round(np.nanmedian((yours[both] - target[both]) / 12)))

    on = np.abs(diff[both]) < ON_PITCH
    # Coverage counts only the stretch of the song the take covers.
    span = (t >= report["phrases"][0]["start"] + report["phrases"][0]["offset"]) & \
           (t <= report["phrases"][-1]["end"] + report["phrases"][-1]["offset"])
    orig_voiced = ~np.isnan(orig) & span
    phrases = []
    for p in report["phrases"]:
        a, b = p["start"] + p["offset"], p["end"] + p["offset"]  # where the phrase sits in the song
        m = both & (t >= a) & (t <= b)
        if m.sum() >= 5:
            phrases.append({"start": round(a, 2), "end": round(b, 2),
                            "on_pitch": round(float(np.mean(np.abs(diff[m]) < ON_PITCH)), 2),
                            "median_cents": round(float(np.median(diff[m])))})

    # Chart: every other frame, original moved into the take's key and octave so the lines overlay.
    step = 2
    chart = {"t": np.round(t[::step], 3).tolist(),
             "orig": [None if np.isnan(v) else round(float(v), 2) for v in (target + 12 * octave)[::step]],
             "yours": [None if np.isnan(v) else round(float(v), 2) for v in yours[::step]],
             "diff": [None if np.isnan(v) else round(float(v)) for v in diff[::step]]}

    result = {
        "on_pitch": round(float(on.mean()), 3),            # share of compared frames within 50 cents
        "median_cents": round(float(np.median(diff[both]))),  # + = sharp, - = flat
        "typical_miss": round(float(np.median(np.abs(diff[both])))),
        "coverage": round(float((both & span).sum() / max(1, orig_voiced.sum())), 2),  # of the original's singing in that stretch, the share you sang over
        "key_shift": key_shift,
        "octave": octave,
        "phrases": phrases,
        "sync": {k: v for k, v in report.items() if k != "phrases"},
        "chart": chart,
    }
    (out_dir / "pitch.json").write_text(json.dumps(result))
    return result


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 3:
        sys.exit(__doc__)
    r = pitch_match(*args, take_has_music="--take-has-music" in sys.argv)
    print(json.dumps({k: v for k, v in r.items() if k != "chart"}, indent=1))
