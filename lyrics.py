"""Word-timed lyrics for a song: LRC (karaoke players), word-level LRC, SRT (video subtitles), JSON.

The vocal is separated with Demucs and transcribed with Whisper (faster-whisper, "small",
any language). Without lyrics, Whisper's own lines and word times are used.

With the real lyrics pasted in, they're used instead of what Whisper heard. Whisper's words are
matched to the lyrics in order, which says roughly where each line is sung. For English, each
line is then pinned word by word with forced alignment (align_words.py, from the captcha-karaoke
prototype), because Whisper's own word times on singing can be seconds off. Words that still
have no time are spread evenly between their neighbours.

  python lyrics.py song.mp3 out_dir/ [lyrics.txt] [--is-vocal]
"""

import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # find sibling modules even under python -I
import sync  # noqa: E402

MODEL = "small"
PAD = 1.0        # seconds of overlap allowed with the neighbouring lines when pinning a line
MAX_REACH = 8.0  # seconds a window may reach beyond the words Whisper matched in it
CHUNK = 40.0     # seconds of song pinned in one pass (the speech model's memory grows with length)


def norm(word):
    return re.sub(r"[^\w]", "", word.lower().replace("’", "").replace("'", ""))


def close(a, b):
    return a == b or (len(a) > 2 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.75)


def transcribe(vocal):
    from faster_whisper import WhisperModel

    model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    # No voice filter: it drops sung passages it takes for noise (seen on separated vocals).
    segments, info = model.transcribe(str(vocal), word_timestamps=True, vad_filter=False,
                                      condition_on_previous_text=False)
    lines = []
    for s in segments:
        words = [(w.word.strip(), w.start, w.end) for w in (s.words or []) if norm(w.word)]
        if words:
            lines.append(words)
    return lines, info.language


def match(heard, lyric):
    """In-order global alignment of heard words to lyric words. Returns {lyric index: heard index}."""
    n, m = len(lyric), len(heard)
    gap = -0.6
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = i * gap
    for j in range(1, m + 1):
        score[0][j] = j * gap * 0.5  # extra heard words (ad-libs) cost less
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = score[i - 1][j - 1] + (2.0 if close(lyric[i - 1], heard[j - 1]) else -1.0)
            score[i][j] = max(diag, score[i - 1][j] + gap, score[i][j - 1] + gap * 0.5)
    pairs, i, j = {}, n, m
    while i and j:
        hit = close(lyric[i - 1], heard[j - 1])
        if score[i][j] == score[i - 1][j - 1] + (2.0 if hit else -1.0):
            if hit:
                pairs[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif score[i][j] == score[i - 1][j] + gap:
            i -= 1
        else:
            j -= 1
    return pairs


def fill(times, count):
    """Spread untimed words evenly between the nearest timed words either side."""
    known = sorted(times)
    for k in range(count):
        if k in times:
            continue
        before = max((p for p in known if p < k), default=None)
        after = min((p for p in known if p > k), default=None)
        if before is None or after is None:
            continue
        a, b = times[before][1], times[after][0]
        step = max(0.05, (b - a) / (after - before))
        times[k] = (a + step * (k - before - 1), a + step * (k - before))
    return times


def with_lyrics(vocal, heard_lines, language, text):
    from align_words import align as force_align

    lines = [l.split() for l in text.splitlines() if l.split()]
    flat = [(li, w) for li, line in enumerate(lines) for w in line]
    heard = [w for line in heard_lines for w in line]
    pairs = match([norm(h[0]) for h in heard], [norm(w) for _, w in flat])
    times = {k: heard[j][1:] for k, j in pairs.items()}
    pinned = 0
    if language == "en":
        # Rough place of each line from the words Whisper got right.
        heard_at = {}
        for k, j in pairs.items():
            li = flat[k][0]
            a, b = heard[j][1], heard[j][2]
            lo, hi = heard_at.get(li, (a, b))
            heard_at[li] = (min(lo, a), max(hi, b))
        # Lines are pinned together in chunks of up to CHUNK seconds, so neighbouring words hold
        # each other in place (one line alone drifts). A line Whisper misheard is still found:
        # the words are known, only the place isn't. Each chunk's window runs from just before
        # the previous chunk's last heard word to just after the next chunk's first.
        anchored = sorted(heard_at)
        chunks, current = [], []
        for li in range(len(lines)):
            if current and li in heard_at:
                first = next((heard_at[l][0] for l in current if l in heard_at), None)
                if first is not None and heard_at[li][1] - first > CHUNK:
                    chunks.append(current)
                    current = []
            current.append(li)
        if current:
            chunks.append(current)
        for chunk in chunks:
            before = [heard_at[l][1] for l in anchored if l < chunk[0]]
            after = [heard_at[l][0] for l in anchored if l > chunk[-1]]
            own = [heard_at[l] for l in chunk if l in heard_at]
            lo = max(before) - PAD if before else 0.0
            hi = min(after) + PAD if after else None
            if own:  # don't let the window stretch across a long instrumental
                lo = max(lo, own[0][0] - MAX_REACH)
                hi = min(hi, own[-1][1] + MAX_REACH) if hi is not None else own[-1][1] + MAX_REACH
            elif hi is None or hi - lo > 2 * MAX_REACH:
                continue  # nothing nearby to go on
            idx = [k for k, (l, _) in enumerate(flat) if l in chunk]
            forced = force_align(vocal, [flat[k][1] for k in idx], max(0.0, lo), hi)
            if sum(1 for f in forced if f) >= 0.6 * len(idx):
                for k, f in zip(idx, forced):
                    if f:
                        times[k] = f
                pinned += len(chunk)
    times = fill(times, len(flat))
    out = []
    for li in range(len(lines)):
        words = [(flat[k][1], *times[k]) for k, (l, _) in enumerate(flat) if l == li and k in times]
        if words:
            out.append(words)
    stats = {"lyric_words": len(flat), "matched": len(pairs), "lines_pinned": pinned, "lyric_lines": len(lines)}
    return out, stats


def stamp(t, srt=False):
    m, s = divmod(max(0.0, t), 60)
    if srt:
        h, m = divmod(int(m), 60)
        return f"{h:02d}:{m:02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}"
    return f"{int(m):02d}:{s:05.2f}"


def write(lines, out_dir):
    out_dir = Path(out_dir)
    text = lambda line: " ".join(w for w, _, _ in line)
    (out_dir / "lyrics.lrc").write_text("".join(f"[{stamp(l[0][1])}]{text(l)}\n" for l in lines))
    (out_dir / "lyrics_words.lrc").write_text("".join(
        f"[{stamp(l[0][1])}]" + " ".join(f"<{stamp(s)}>{w}" for w, s, _ in l) + f" <{stamp(l[-1][2])}>\n" for l in lines))
    (out_dir / "lyrics.srt").write_text("".join(
        f"{i}\n{stamp(l[0][1], True)} --> {stamp(l[-1][2], True)}\n{text(l)}\n\n" for i, l in enumerate(lines, 1)))
    data = [{"start": round(l[0][1], 2), "end": round(l[-1][2], 2),
             "words": [{"w": w, "start": round(s, 2), "end": round(e, 2)} for w, s, e in l]} for l in lines]
    (out_dir / "lyrics.json").write_text(json.dumps(data, ensure_ascii=False))
    return data


def lyrics_timing(song_path, out_dir, lyrics_text=None, is_vocal=False):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    vocal = song_path if is_vocal else sync.separate(song_path, out_dir / "work")[0]
    heard, language = transcribe(vocal)
    if not heard:
        raise RuntimeError("Whisper didn't hear any words in the vocal.")
    if lyrics_text and lyrics_text.strip():
        lines, stats = with_lyrics(vocal, heard, language, lyrics_text)
        method = "your lyrics, pinned word by word" if stats["lines_pinned"] else "your lyrics, timed from what Whisper heard"
    else:
        lines, stats = heard, {}
        method = "what Whisper heard"
    data = write(lines, out_dir)
    return {"language": language, "method": method, "lines": data, **stats}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) not in (2, 3):
        sys.exit(__doc__)
    text = Path(args[2]).read_text() if len(args) == 3 else None
    r = lyrics_timing(args[0], args[1], text, "--is-vocal" in sys.argv)
    print(json.dumps({k: v for k, v in r.items() if k != "lines"}, indent=1))
    for line in r["lines"]:
        print(f"{line['start']:7.2f}  " + " ".join(w["w"] for w in line["words"]))
