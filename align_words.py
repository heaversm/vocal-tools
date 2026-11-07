"""Forced alignment: where each word of known lyrics is sung in a vocal recording.

Whisper's own word times are unreliable on singing: with its voice filter on, it snaps the first
word of a passage to where the passage starts (Sweet Caroline's "touching" came out ~4 s early),
and a word it mishears gets no time at all. Here the lyrics are given, so a speech model only has
to say, frame by frame, how likely each letter is; the standard CTC alignment then finds the one
path through those frames that spells the lyrics in order, and every word gets a start and end.

  from align_words import align
  times = align("vocals.wav", ["hands", "touching", "hands"], start=0.0, end=30.0)
  # [(start, end) or None for each word], in seconds from the start of the file
"""

import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np

MODEL = "facebook/wav2vec2-base-960h"  # English letters; a CTC model, which is what alignment needs
RATE = 16000
_loaded = None


def _model():
    global _loaded
    if _loaded is None:
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

        processor = Wav2Vec2Processor.from_pretrained(MODEL)
        model = Wav2Vec2ForCTC.from_pretrained(MODEL).eval()
        _loaded = (processor, model)
    return _loaded


def _audio(path, start, end):
    """The stretch [start, end] of a recording as 16 kHz mono samples."""
    import soundfile as sf

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "a.wav"
        cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}"]
        if end is not None:
            cmd += ["-to", f"{end:.3f}"]
        cmd += ["-i", str(path), "-ac", "1", "-ar", str(RATE), str(out)]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        data, _ = sf.read(str(out), dtype="float32")
    return data


def _letters(word):
    return re.sub(r"[^A-Z']", "", word.upper().replace("’", "'"))


def align(path, words, start=0.0, end=None):
    import torch

    processor, model = _model()
    samples = _audio(path, start, end)
    with torch.no_grad():
        inputs = processor(samples, sampling_rate=RATE, return_tensors="pt")
        emissions = torch.log_softmax(model(inputs.input_values).logits, dim=-1)[0].numpy()
    vocab = processor.tokenizer.get_vocab()
    blank = vocab[processor.tokenizer.pad_token]
    sep = vocab["|"]

    # The transcript as tokens, words separated by "|", remembering which word each token is in.
    tokens, owner = [], []
    for i, w in enumerate(words):
        letters = [c for c in _letters(w) if c in vocab]
        if not letters:
            continue
        if tokens:
            tokens.append(sep)
            owner.append(None)
        tokens += [vocab[c] for c in letters]
        owner += [i] * len(letters)
    frames, n = emissions.shape[0], len(tokens)
    if n == 0 or frames < n:
        return [None] * len(words)

    # Trellis: best log-probability of having emitted the first j tokens after t frames.
    trellis = np.full((frames + 1, n + 1), -np.inf)
    trellis[0, 0] = 0.0
    trellis[1:, 0] = np.cumsum(emissions[:, blank])
    tok = np.array(tokens)
    for t in range(frames):
        stay = trellis[t, 1:] + emissions[t, blank]
        move = trellis[t, :-1] + emissions[t, tok]
        trellis[t + 1, 1:] = np.maximum(stay, move)

    # Walk back from the end: which frame each token was emitted at.
    t, j = frames, n
    at = [0] * n
    while j > 0 and t > 0:
        stay = trellis[t - 1, j] + emissions[t - 1, blank]
        move = trellis[t - 1, j - 1] + emissions[t - 1, tokens[j - 1]]
        if move >= stay:
            at[j - 1] = t - 1
            j -= 1
        t -= 1

    seconds_per_frame = len(samples) / RATE / frames
    out = [None] * len(words)
    for k, word in enumerate(owner):
        if word is None:
            continue
        s, e = start + at[k] * seconds_per_frame, start + (at[k] + 1) * seconds_per_frame
        out[word] = (s, e) if out[word] is None else (out[word][0], e)
    # A word ends where the next one starts, so long held notes keep their full length.
    starts = [o[0] if o else None for o in out]
    for i, o in enumerate(out):
        if o is None:
            continue
        nxt = next((s for s in starts[i + 1:] if s is not None), None)
        if nxt is not None and nxt - o[1] < 1.5:
            out[i] = (o[0], max(o[1], nxt))
    return out
