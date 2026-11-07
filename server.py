"""Local audio tools on one page, nothing leaves the machine:
  Split    - drop a song, get the vocals and the instrumental (Demucs).
  Convert  - drop a performance and a target voice, get the performance sung/spoken in that voice (Seed-VC).
  Sync     - drop a song and a recording of someone singing it, get the singing lined up with the song (sync.py).
  Pitch    - same inputs as Sync, plus a score for how closely the singing follows the original melody (pitch.py).
  Tune     - same inputs again; pulls the singing onto the original singer's notes (tune.py).
  Key      - drop a song, get it higher or lower at the same speed, optionally the instrumental too (key.py).
  Lyrics   - drop a song (and optionally paste its lyrics), get word-timed LRC/SRT files (lyrics.py).
  Library  - every result, with play, download, delete, and a link to the folder on disk.

Install once with ./setup.sh, then open the Vocal Tools app (or run ./start.command).

Files go in data/, which git ignores:
  data/uploads/<id>/  files dropped on Convert and Sync, deleted as soon as the job ends
  data/results/<id>/  each result's audio plus meta.json; anything older than KEEP_DAYS is deleted at startup
"""

import http.server
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # for sync.py; start.command runs Python with -I, which leaves this folder out
DATA = HERE / "data"
UPLOADS = DATA / "uploads"
RESULTS = DATA / "results"
SEED_VC = HERE / "vendor" / "seed-vc"
KEEP_DAYS = 7
ID = r"[0-9a-f]{32}"
JOBS = {}  # job id -> {"state": "running" | "done" | "failed", ...}


# ---------- storage ----------

def new_id():
    return uuid.uuid4().hex


def save_upload(data, filename):
    uid = new_id()
    folder = UPLOADS / uid
    folder.mkdir(parents=True)
    (folder / f"input{Path(filename).suffix.lower() or '.audio'}").write_bytes(data)
    (folder / "name.txt").write_text(filename)
    return uid


def upload(uid):
    """(path, original filename) of an upload, or None."""
    if not re.fullmatch(ID, uid or ""):
        return None
    found = list((UPLOADS / uid).glob("input.*"))
    return (found[0], (UPLOADS / uid / "name.txt").read_text()) if found else None


def remove(folder):
    shutil.rmtree(folder, ignore_errors=True)


def stem(name):
    return Path(name).stem


def finish(job, kind, title, files, note=""):
    """Record a result so the library can list it. files: [(label, file name, download name)]."""
    meta = {"id": job, "kind": kind, "title": title, "note": note, "created": time.time(),
            "files": [{"label": l, "file": f, "download": d} for l, f, d in files]}
    (RESULTS / job / "meta.json").write_text(json.dumps(meta))
    return meta


def size(folder):
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())


def library():
    items = []
    for folder in RESULTS.iterdir() if RESULTS.exists() else []:
        meta_file = folder / "meta.json"
        if not meta_file.exists():
            continue
        meta = json.loads(meta_file.read_text())
        meta.update(id=folder.name, bytes=size(folder), folder=str(folder),
                    when=datetime.fromtimestamp(meta["created"]).strftime("%b %-d, %-I:%M %p"))
        items.append(meta)
    items.sort(key=lambda m: m["created"], reverse=True)
    return {"items": items, "folder": str(DATA), "bytes": size(DATA) if DATA.exists() else 0, "keep_days": KEEP_DAYS}


def sweep():
    """At startup: delete results older than KEEP_DAYS, and uploads left behind by jobs that never ran."""
    cutoff = time.time() - KEEP_DAYS * 86400
    removed = 0
    for root in (RESULTS, UPLOADS):
        for folder in root.iterdir() if root.exists() else []:
            if folder.stat().st_mtime < cutoff or root is UPLOADS:
                remove(folder)
                removed += 1
    return removed


def reveal(folder):
    """Open a folder in Finder (or the Linux file manager)."""
    subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])


# ---------- tools ----------

def last_line(text, fallback):
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return lines[-1] if lines else fallback


FOUR = [("Vocals", "vocals"), ("Drums", "drums"), ("Bass", "bass"), ("Other", "other")]


def split(data, filename, four=False):
    job = new_id()
    folder = RESULTS / job
    folder.mkdir(parents=True)
    src = folder / f"input{Path(filename).suffix.lower() or '.audio'}"
    src.write_bytes(data)
    try:
        stems = [] if four else ["--two-stems=vocals"]
        r = subprocess.run([sys.executable, "-m", "demucs", *stems, "-n", "htdemucs", "-o", str(folder), str(src)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(last_line(r.stderr, "Demucs failed"))
        for name in ([f"{n}.wav" for _, n in FOUR] if four else ["vocals.wav", "no_vocals.wav"]):
            shutil.move(folder / "htdemucs" / "input" / name, folder / name)
    except Exception:
        remove(folder)
        raise
    remove(folder / "htdemucs")
    src.unlink()
    s = stem(filename)
    if four:
        return finish(job, "Split", filename, [(l, f"{n}.wav", f"{s} - {n}.wav") for l, n in FOUR], "four parts")
    return finish(job, "Split", filename, [("Vocals", "vocals.wav", f"{s} - vocals.wav"),
                                           ("Instrumental", "no_vocals.wav", f"{s} - instrumental.wav")])


def convert(job, source, target, singing, steps, shift):
    (src, src_name), (tgt, tgt_name) = source, target
    folder = RESULTS / job
    out = folder / "out"
    cmd = [sys.executable, "inference.py", "--source", str(src), "--target", str(tgt), "--output", str(out),
           "--diffusion-steps", str(steps), "--f0-condition", str(singing),
           "--semi-tone-shift", str(shift), "--fp16", "False"]
    try:
        r = subprocess.run(cmd, cwd=SEED_VC, capture_output=True, text=True)
        wavs = list(out.glob("*.wav"))
        if r.returncode != 0 or not wavs:
            raise RuntimeError(last_line(r.stderr, "Seed-VC failed"))
        shutil.move(wavs[0], folder / "converted.wav")
        remove(out)
        note = f"{'singing' if singing else 'speech'}, {steps} steps" + (f", shifted {shift:+d}" if shift else "")
        JOBS[job] = {"state": "done", **finish(job, "Convert", f"{src_name} in the voice of {tgt_name}",
                     [("Converted", "converted.wav", f"{stem(src_name)} - as {stem(tgt_name)}.wav")], note)}
    except Exception as e:
        remove(folder)
        JOBS[job] = {"state": "failed", "error": str(e)}
    finally:
        remove(src.parent)
        remove(tgt.parent)


def run_sync(job, song, take, take_has_music):
    (song_path, song_name), (take_path, take_name) = song, take
    folder = RESULTS / job
    try:
        import sync  # librosa is slow to import, so only when needed
        report = sync.sync(song_path, take_path, folder, take_has_music)
        remove(folder / "work")
        s = stem(take_name)
        JOBS[job] = {"state": "done", "report": report, **finish(job, "Sync", f"{take_name} synced to {song_name}",
                     [("Mix", "synced_mix.wav", f"{s} - synced mix.wav"),
                      ("Your vocal", "synced_vocal.wav", f"{s} - synced vocal.wav")])}
    except Exception as e:
        remove(folder)
        JOBS[job] = {"state": "failed", "error": str(e)}
    finally:
        remove(song_path.parent)
        remove(take_path.parent)


def run_pitch(job, song, take, take_has_music):
    (song_path, song_name), (take_path, take_name) = song, take
    folder = RESULTS / job
    try:
        import pitch
        r = pitch.pitch_match(song_path, take_path, folder, take_has_music)
        remove(folder / "work")
        (folder / "sync.json").unlink(missing_ok=True)
        s = stem(take_name)
        meta = finish(job, "Pitch", f"{take_name} against {song_name}",
                      [("Mix", "synced_mix.wav", f"{s} - synced mix.wav"),
                       ("Your vocal", "synced_vocal.wav", f"{s} - synced vocal.wav")],
                      f"{round(r['on_pitch'] * 100)}% on pitch")
        JOBS[job] = {"state": "done", "pitch": r, **meta}
    except Exception as e:
        remove(folder)
        JOBS[job] = {"state": "failed", "error": str(e)}
    finally:
        remove(song_path.parent)
        remove(take_path.parent)


def run_tune(job, song, take, take_has_music, strength):
    (song_path, song_name), (take_path, take_name) = song, take
    folder = RESULTS / job
    try:
        import tune
        r = tune.tune(song_path, take_path, folder, strength, take_has_music)
        remove(folder / "work")
        for leftover in ("sync.json", "pitch.json", "synced_mix.wav"):
            (folder / leftover).unlink(missing_ok=True)
        s = stem(take_name)
        meta = finish(job, "Tune", f"{take_name} tuned to {song_name}",
                      [("Tuned mix", "tuned_mix.wav", f"{s} - tuned mix.wav"),
                       ("Tuned vocal", "tuned_vocal.wav", f"{s} - tuned vocal.wav"),
                       ("Before", "synced_vocal.wav", f"{s} - synced vocal.wav")],
                      f"{round(r['before'] * 100)}% → {round(r['after'] * 100)}% on pitch, strength {round(strength * 100)}%")
        JOBS[job] = {"state": "done", "tune": r, **meta}
    except Exception as e:
        remove(folder)
        JOBS[job] = {"state": "failed", "error": str(e)}
    finally:
        remove(song_path.parent)
        remove(take_path.parent)


def run_key(job, song, semitones, instrumental):
    song_path, song_name = song
    folder = RESULTS / job
    try:
        import key
        key.change_key(song_path, semitones, folder, instrumental)
        remove(folder / "work")
        s, label = stem(song_name), f"{semitones:+g}"
        files = [("Song", "shifted.wav", f"{s} ({label}).wav")]
        if instrumental:
            files.append(("Instrumental", "shifted_instrumental.wav", f"{s} - instrumental ({label}).wav"))
        JOBS[job] = {"state": "done", **finish(job, "Key", f"{song_name}, {label} semitones", files)}
    except Exception as e:
        remove(folder)
        JOBS[job] = {"state": "failed", "error": str(e)}
    finally:
        remove(song_path.parent)


def run_lyrics(job, song, text):
    song_path, song_name = song
    folder = RESULTS / job
    try:
        import lyrics
        r = lyrics.lyrics_timing(song_path, folder, text)
        remove(folder / "work")
        s = stem(song_name)
        meta = finish(job, "Lyrics", song_name,
                      [("LRC", "lyrics.lrc", f"{s}.lrc"), ("Word LRC", "lyrics_words.lrc", f"{s} (words).lrc"),
                       ("SRT", "lyrics.srt", f"{s}.srt")], r["method"])
        JOBS[job] = {"state": "done", "lyrics": r, **meta}
    except Exception as e:
        remove(folder)
        JOBS[job] = {"state": "failed", "error": str(e)}
    finally:
        remove(song_path.parent)


def start(target, *args):
    job = new_id()
    (RESULTS / job).mkdir(parents=True)
    JOBS[job] = {"state": "running"}
    threading.Thread(target=target, args=(job, *args), daemon=True).start()
    return {"job": job}


# ---------- web ----------

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the Terminal window quiet except for errors
        if not args or not str(args[1]).startswith(("2", "3")):
            super().log_message(fmt, *args)

    def send(self, code, body, kind="application/json"):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def filename(self):
        return unquote(self.headers.get("X-Filename", "upload"))

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if url.path == "/library":
            return self.send(200, library())
        m = re.fullmatch(rf"/files/({ID})/([a-z_]+\.(wav|json|lrc|srt))", url.path)
        if m and m.group(2) != "meta.json" and (RESULTS / m.group(1) / m.group(2)).is_file():
            kind = {"wav": "audio/wav", "json": "application/json"}.get(m.group(3), "text/plain; charset=utf-8")
            return self.send(200, (RESULTS / m.group(1) / m.group(2)).read_bytes(), kind)
        m = re.fullmatch(rf"/status/({ID})", url.path)
        if m and m.group(1) in JOBS:
            return self.send(200, JOBS[m.group(1)])
        self.send(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}

        if url.path in ("/split", "/upload"):
            data = self.body()
            if not data:
                return self.send(400, {"error": "empty upload"})
            if url.path == "/upload":  # held for /convert or /sync
                return self.send(200, {"id": save_upload(data, self.filename())})
            try:
                return self.send(200, split(data, self.filename(), q.get("four") == "1"))
            except Exception as e:
                return self.send(500, {"error": str(e)})

        if url.path in ("/convert", "/sync", "/pitch", "/tune"):
            keys = ("source", "target") if url.path == "/convert" else ("song", "take")
            files = [upload(q.get(k)) for k in keys]
            if not all(files):
                return self.send(400, {"error": "upload missing; drop the files again"})
            if url.path == "/convert":
                return self.send(200, start(convert, *files, q.get("singing") == "1",
                                            max(1, min(100, int(q.get("steps", 30)))),
                                            max(-24, min(24, int(q.get("shift", 0))))))
            music = q.get("music") == "1"
            if url.path == "/tune":
                return self.send(200, start(run_tune, *files, music, max(0.0, min(1.0, float(q.get("strength", 0.8))))))
            return self.send(200, start(run_sync if url.path == "/sync" else run_pitch, *files, music))

        if url.path == "/lyrics":
            song = upload(q.get("song"))
            if not song:
                return self.send(400, {"error": "upload missing; drop the file again"})
            return self.send(200, start(run_lyrics, song, self.body().decode("utf-8", "replace")))

        if url.path == "/key":
            song = upload(q.get("song"))
            if not song:
                return self.send(400, {"error": "upload missing; drop the file again"})
            semitones = max(-12.0, min(12.0, float(q.get("semitones", 0))))
            return self.send(200, start(run_key, song, semitones, q.get("inst") == "1"))

        m = re.fullmatch(rf"/delete/({ID})", url.path)
        if m:
            if JOBS.get(m.group(1), {}).get("state") == "running":
                return self.send(409, {"error": "still running"})
            remove(RESULTS / m.group(1))
            return self.send(200, library())
        if url.path == "/clear":
            running = {j for j, s in JOBS.items() if s.get("state") == "running"}
            for folder in RESULTS.iterdir() if RESULTS.exists() else []:
                if folder.name not in running:
                    remove(folder)
            return self.send(200, library())
        if url.path == "/reveal":
            target = RESULTS / q["id"] if re.fullmatch(ID, q.get("id", "")) else DATA
            reveal(target if target.exists() else DATA)
            return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8010
    RESULTS.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    removed = sweep()
    print(f"Vocal tools on http://localhost:{port}")
    print(f"Files are in {DATA}" + (f" (cleared {removed} old item{'s' * (removed != 1)})" if removed else ""))
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
