"""Transcribes a video (or audio file): recognizes the words spoken or sung and writes them out.

The audio is pulled out with ffmpeg and transcribed with Whisper (faster-whisper, via lyrics.py),
in any language. If it has music in it, the voice is separated from the music first with Demucs,
which helps Whisper hear the words. Output: a plain-text transcript, an SRT subtitle file (long lines
split so each subtitle is readable), and the timed words as JSON.

  python transcribe.py video.mp4 out_dir/ [--has-music]
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lyrics  # noqa: E402


MAX_CHARS = 84  # longest subtitle: two rows of about 42 characters, the usual limit


def subtitles(lines):
    """Split timed lines into subtitles of at most MAX_CHARS, each timed by its own words."""
    subs = []
    for line in lines:
        cur = []
        for w in line["words"]:
            if cur and len(" ".join(x["w"] for x in cur + [w])) > MAX_CHARS:
                subs.append(cur)
                cur = []
            cur.append(w)
        if cur:
            subs.append(cur)
    return "".join(f"{i}\n{lyrics.stamp(c[0]['start'], True)} --> {lyrics.stamp(c[-1]['end'], True)}\n"
                   f"{' '.join(w['w'] for w in c)}\n\n" for i, c in enumerate(subs, 1))


def transcribe(video, out_dir, has_music=False):
    out_dir = Path(out_dir)
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    audio = work / "audio.wav"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "2", "-ar", "44100", str(audio)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not audio.exists() or audio.stat().st_size < 1000:
        raise RuntimeError("Couldn't find any sound in that file.")
    result = lyrics.lyrics_timing(audio, out_dir, is_vocal=not has_music)
    text = "\n".join(" ".join(w["w"] for w in line["words"]) for line in result["lines"])
    (out_dir / "transcript.txt").write_text(text + "\n")
    (out_dir / "transcript.srt").write_text(subtitles(result["lines"]))
    for leftover in ("lyrics.lrc", "lyrics_words.lrc", "lyrics.srt"):
        (out_dir / leftover).unlink(missing_ok=True)
    return {**result, "text": text}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit(__doc__)
    r = transcribe(args[0], args[1], "--has-music" in sys.argv)
    print(f"Language: {r['language']}\n\n{r['text']}")
