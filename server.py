"""Two local audio tools on one page, nothing leaves the machine:
  Split    - drop a song, get the vocals and the instrumental (Demucs).
  Convert  - drop a performance and a target voice, get the performance sung/spoken in that voice (Seed-VC).

Install once with ./setup.sh, then start with ./start.command (or double-click it in Finder).
Uploads and results go in data/, which git ignores.
"""

import http.server
import json
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
WORK = HERE / "data"
SEED_VC = HERE / "vendor" / "seed-vc"
FILES = {"vocals.wav", "no_vocals.wav", "converted.wav"}
JOBS = {}  # job id -> {"state": "running" | "done" | "failed", ...}


def save_upload(data, filename):
    job = uuid.uuid4().hex
    folder = WORK / job
    folder.mkdir(parents=True)
    src = folder / f"input{Path(filename).suffix.lower() or '.audio'}"
    src.write_bytes(data)
    return job, src


def last_line(text, fallback):
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return lines[-1] if lines else fallback


def split(data, filename):
    job, src = save_upload(data, filename)
    folder = src.parent
    r = subprocess.run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", str(folder), str(src)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(last_line(r.stderr, "Demucs failed"))
    for name in ("vocals.wav", "no_vocals.wav"):
        shutil.move(folder / "htdemucs" / "input" / name, folder / name)
    shutil.rmtree(folder / "htdemucs")
    return {"vocals": f"/jobs/{job}/vocals.wav", "instrumental": f"/jobs/{job}/no_vocals.wav"}


def convert(job, source, target, singing, steps, shift):
    folder = WORK / job
    out = folder / "out"
    cmd = [sys.executable, "inference.py",
           "--source", str(source), "--target", str(target), "--output", str(out),
           "--diffusion-steps", str(steps), "--f0-condition", str(singing),
           "--semi-tone-shift", str(shift), "--fp16", "False"]
    try:
        r = subprocess.run(cmd, cwd=SEED_VC, capture_output=True, text=True)
        wavs = list(out.glob("*.wav"))
        if r.returncode != 0 or not wavs:
            raise RuntimeError(last_line(r.stderr, "Seed-VC failed"))
        shutil.move(wavs[0], folder / "converted.wav")
        shutil.rmtree(out)
        JOBS[job] = {"state": "done", "converted": f"/jobs/{job}/converted.wav"}
    except Exception as e:
        JOBS[job] = {"state": "failed", "error": str(e)}


class Handler(http.server.BaseHTTPRequestHandler):
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

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        m = re.fullmatch(r"/jobs/([0-9a-f]{32})/([a-z_]+\.wav)", url.path)
        if m and m.group(2) in FILES and (WORK / m.group(1) / m.group(2)).exists():
            return self.send(200, (WORK / m.group(1) / m.group(2)).read_bytes(), "audio/wav")
        m = re.fullmatch(r"/status/([0-9a-f]{32})", url.path)
        if m and m.group(1) in JOBS:
            return self.send(200, JOBS[m.group(1)])
        self.send(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/split":
            data = self.body()
            if not data:
                return self.send(400, {"error": "empty upload"})
            try:
                return self.send(200, split(data, self.headers.get("X-Filename", "upload")))
            except Exception as e:
                return self.send(500, {"error": str(e)})
        if url.path == "/upload":  # store a file for /convert, return its id
            data = self.body()
            if not data:
                return self.send(400, {"error": "empty upload"})
            job, _ = save_upload(data, self.headers.get("X-Filename", "upload"))
            return self.send(200, {"id": job})
        if url.path == "/convert":
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            paths = {}
            for key in ("source", "target"):
                if not re.fullmatch(r"[0-9a-f]{32}", q.get(key, "")):
                    return self.send(400, {"error": f"missing {key}"})
                found = list((WORK / q[key]).glob("input.*"))
                if not found:
                    return self.send(400, {"error": f"{key} not found"})
                paths[key] = found[0]
            job = uuid.uuid4().hex
            (WORK / job).mkdir(parents=True)
            JOBS[job] = {"state": "running"}
            args = (job, paths["source"], paths["target"], q.get("singing") == "1",
                    max(1, min(100, int(q.get("steps", 30)))), max(-24, min(24, int(q.get("shift", 0)))))
            threading.Thread(target=convert, args=args, daemon=True).start()
            return self.send(200, {"job": job})
        self.send(404, {"error": "not found"})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8010
    WORK.mkdir(parents=True, exist_ok=True)
    print(f"Vocal tools on http://localhost:{port}")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
