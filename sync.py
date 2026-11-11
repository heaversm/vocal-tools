"""Lines up a recording of someone singing with the song they sang, by sound rather than lyrics.

The song's vocal is separated with Demucs. Both vocals become a sequence of pitch-class
frames (which of the 12 notes is sounding, plus whether anyone is singing at all), so a take
in another octave still matches. If the take is in a different key, the shift that fits best
is found first. Dynamic time warping then finds where the take sits in the song. The take is
cut into phrases at its silences, and each phrase is moved by its own offset, so a singer who
rushed or stretched the gaps between lines still lands on time. Nothing is time-stretched:
within a phrase, the singer's own timing is kept.

  python sync.py song.mp3 take.m4a out_dir/ [--take-has-music]
"""

import json
import statistics
import subprocess
import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

SR = 22050          # analysis rate
HOP = 512           # analysis frame, about 23 ms
COARSE = 4          # frames per step when testing the 12 key shifts
OUT_SR = 44100
MIN_PHRASE = 0.25   # seconds; shorter sounds are breaths or clicks
JOIN_GAP = 0.2      # seconds; silences shorter than this stay inside a phrase


def separate(path, work):
    """Demucs two-stem split. Returns (vocals.wav, no_vocals.wav)."""
    work.mkdir(parents=True, exist_ok=True)
    out = work / "htdemucs" / Path(path).stem
    if not (out / "vocals.wav").exists():
        r = subprocess.run([sys.executable, "-m", "demucs", "-d", "cpu", "--two-stems=vocals", "-n", "htdemucs",
                            "-o", str(work), str(path)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("Demucs failed: " + (r.stderr.strip().splitlines() or ["?"])[-1])
    return out / "vocals.wav", out / "no_vocals.wav"


def features(y):
    """Per frame: 12 pitch-class weights while singing, or a lone 'silent' flag."""
    chroma = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP)
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    n = min(chroma.shape[1], len(rms))
    chroma, rms = chroma[:, :n], rms[:n]
    singing = rms > max(1e-4, 0.1 * np.percentile(rms, 95))
    chroma = chroma / (np.linalg.norm(chroma, axis=0, keepdims=True) + 1e-9)
    return np.vstack([chroma * singing, (~singing)[None].astype(float)])


def align(query, ref):
    """Subsequence DTW: all of `query` matched to the best stretch of `ref`.
    Returns the path as (query frame, ref frame) pairs, start to end, and the mean cost per step."""
    cost = 1 - query.T @ ref  # cosine distance; features are unit length or a one-hot silence flag
    D, wp = librosa.sequence.dtw(C=cost, subseq=True)
    wp = wp[::-1]
    return wp, float(D[wp[-1][0], wp[-1][1]] / len(wp))


def best_key_shift(take, song):
    """Semitones to move the take's pitch classes so they line up with the song, tested at a coarse rate."""
    t, s = take[:, ::COARSE], song[:, ::COARSE]
    query, ref = (t, s) if t.shape[1] <= s.shape[1] else (s, t)
    scores = {}
    for shift in range(-5, 7):
        rolled = np.vstack([np.roll(t[:12], shift, axis=0), t[12:]])
        q, r = (rolled, s) if query is t else (s, rolled)
        scores[shift] = align(q, r)[1]
    return min(scores, key=scores.get), scores


def phrases(y):
    """(start, end) in seconds of each sung phrase in the take."""
    spans = librosa.effects.split(y, top_db=35, frame_length=2048, hop_length=HOP) / SR
    merged = []
    for a, b in spans:
        if merged and a - merged[-1][1] < JOIN_GAP:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a >= MIN_PHRASE]


def write_mix(vocal, inst, song_vocal, out):
    """Vocal (mono, OUT_SR) over the instrumental (channels x samples), at the original singer's loudness."""
    orig, _ = librosa.load(song_vocal, sr=OUT_SR, mono=True)
    n = min(len(vocal), inst.shape[1])
    voiced = vocal[:n][vocal[:n] != 0]
    gain = np.sqrt(np.mean(orig ** 2) / (np.mean(voiced ** 2) + 1e-12)) if voiced.size else 1.0
    mix = inst[:, :n] + vocal[None, :n] * gain
    peak = np.abs(mix).max()
    if peak > 0.99:
        mix *= 0.99 / peak
    sf.write(out, mix.T, OUT_SR)


def sync(song_path, take_path, out_dir, take_has_music=False):
    out_dir = Path(out_dir)
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    song_vocal, instrumental = separate(song_path, work / "song")
    take_voice = separate(take_path, work / "take")[0] if take_has_music else take_path  # own folder: uploads share a file name

    sv, _ = librosa.load(song_vocal, sr=SR, mono=True)
    tv, _ = librosa.load(take_voice, sr=SR, mono=True)
    fs, ft = features(sv), features(tv)

    shift, _ = best_key_shift(ft, fs)
    ft = np.vstack([np.roll(ft[:12], shift, axis=0), ft[12:]])

    # The shorter recording is matched against a stretch of the longer one.
    take_is_query = ft.shape[1] <= fs.shape[1]
    wp, cost = align(ft, fs) if take_is_query else align(fs, ft)
    if not take_is_query:
        wp = wp[:, ::-1]
    take_frames, song_frames = wp[:, 0], wp[:, 1]
    frame = HOP / SR

    # Each phrase moves by the median gap between where it is in the take and where the song has it.
    placed, skipped = [], 0
    for a, b in phrases(tv):
        inside = (take_frames * frame >= a) & (take_frames * frame <= b)
        if inside.sum() < 3:
            skipped += 1  # outside the stretch that matched the song
            continue
        gaps = (song_frames[inside] - take_frames[inside]) * frame
        offset = float(np.median(gaps))
        drift = float(np.median(np.abs(gaps - offset)))
        placed.append({"start": round(a, 2), "end": round(b, 2), "offset": round(offset, 2), "drift": round(drift, 2)})
    if not placed:
        raise RuntimeError("Couldn't find the take anywhere in the song.")

    # Build the aligned vocal at full quality, the same length as the song.
    take_full, _ = librosa.load(take_voice, sr=OUT_SR, mono=True)
    inst, _ = librosa.load(instrumental, sr=OUT_SR, mono=False)
    inst = np.atleast_2d(inst)
    length = inst.shape[1]
    aligned = np.zeros(length, dtype=np.float32)
    fade = int(0.01 * OUT_SR)
    for p in placed:
        a, b = int(max(0, p["start"] - 0.05) * OUT_SR), int((p["end"] + 0.05) * OUT_SR)
        piece = take_full[a:b].copy()
        if len(piece) > 2 * fade:
            ramp = np.linspace(0, 1, fade)
            piece[:fade] *= ramp
            piece[-fade:] *= ramp[::-1]
        at = a + int(p["offset"] * OUT_SR)
        lo, hi = max(0, at), min(length, at + len(piece))
        if hi > lo:
            aligned[lo:hi] += piece[lo - at:hi - at]

    sf.write(out_dir / "synced_vocal.wav", aligned, OUT_SR)
    write_mix(aligned, inst, song_vocal, out_dir / "synced_mix.wav")

    offsets = [p["offset"] for p in placed]
    report = {
        "key_shift": -shift,  # semitones the singer was from the original (+ = higher)
        "phrases_placed": len(placed),
        "phrases_skipped": skipped,
        "median_offset": round(statistics.median(offsets), 2),
        "offset_range": round(max(offsets) - min(offsets), 2),
        "match_cost": round(cost, 3),  # 0 = identical; above about 0.35 the match is doubtful
        "phrases": placed,
    }
    (out_dir / "sync.json").write_text(json.dumps(report, indent=1))
    return report


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 3:
        sys.exit(__doc__)
    print(json.dumps(sync(*args, take_has_music="--take-has-music" in sys.argv), indent=1))
