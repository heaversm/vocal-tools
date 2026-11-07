"""Moves a song up or down in pitch without changing its speed (Rubber Band, via pedalboard).

  python key.py song.mp3 semitones out_dir/ [--instrumental]

--instrumental also splits the song first and returns the instrumental in the new key too.
"""

import sys
from pathlib import Path

import numpy as np
from pedalboard import PitchShift
from pedalboard.io import AudioFile

sys.path.insert(0, str(Path(__file__).resolve().parent))  # find sync.py even under python -I
import sync  # noqa: E402


def shift_file(src, semitones, dest):
    with AudioFile(str(src)) as f:
        audio, sr = f.read(f.frames), f.samplerate
    out = PitchShift(semitones=semitones)(audio, sr)
    peak = np.abs(out).max()
    if peak > 0.99:
        out *= 0.99 / peak
    with AudioFile(str(dest), "w", sr, out.shape[0]) as f:
        f.write(out)


def change_key(song_path, semitones, out_dir, instrumental=False):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    shift_file(song_path, semitones, out_dir / "shifted.wav")
    files = ["shifted.wav"]
    if instrumental:
        _, inst = sync.separate(song_path, out_dir / "work")
        shift_file(inst, semitones, out_dir / "shifted_instrumental.wav")
        files.append("shifted_instrumental.wav")
    return files


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 3:
        sys.exit(__doc__)
    print(change_key(args[0], float(args[1]), args[2], "--instrumental" in sys.argv))
