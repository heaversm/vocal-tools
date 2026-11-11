# Vocal tools

Audio tools on one local web page. Everything runs on your own machine. No accounts, no API keys, nothing uploaded.

- **Split.** Drop a song, get the vocals and the instrumental as separate files, or four parts: vocals, drums, bass and everything else. Uses [Demucs](https://github.com/adefossez/demucs).
- **Convert.** Drop a *performance* (the melody, timing and delivery to keep) and a *target voice* (1–30 s of the voice it should sound like). You get the performance back in the target voice. Uses [Seed-VC](https://github.com/Plachtaa/seed-vc). Works for singing and speech.
- **Sync.** Drop a song and a recording of you singing it. Your singing is lined up with the song and mixed over the instrumental. It matches by melody rather than lyrics, so it works in any language, in a different key or octave, and for a take that covers only part of the song. Each phrase is placed on its own, so rushed or stretched gaps between lines still land on time. It never changes your speed within a phrase.
- **Pitch match.** Same inputs as Sync. After lining your take up, it tracks the pitch of your voice and the original singer's and scores how often you were on the note (within 50 cents, half a semitone), how far you typically missed, and whether you lean sharp or flat. A chart shows your pitch over the original melody. Singing in another key or octave isn't counted against you.
- **Tune to original.** Same inputs again. It pulls your pitch toward the original singer's notes, moment by moment, with a strength control, keeping your voice and timing. Re-synthesis uses the [WORLD](https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder) vocoder. It reports your on-pitch score before and after. It helps off-pitch takes (a real cover went from 62% to 79% at 80% strength) and does nothing for takes that are already accurate.
- **Change key.** Drop a song and choose how many semitones to move it, up or down. The speed stays the same. You can also get the instrumental in the new key, for singing along. Uses [Rubber Band](https://breakfastquay.com/rubberband/) through Spotify's [pedalboard](https://github.com/spotify/pedalboard).
- **Lyrics timing.** Drop a song, optionally paste its lyrics, and get them timed word by word as LRC (line and word level, for karaoke players) and SRT (subtitles). Uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper), plus forced alignment for English when you paste lyrics. Limits: without pasted lyrics it's only as good as what Whisper hears, which on singing is often wrong. With pasted English lyrics, clips up to about 40 s are precise (within 20 ms of hand-checked timings on a test clip); in longer songs some words can be a few seconds off.

## Requirements

- macOS on Apple Silicon (tested on an M2) or Linux (untested). Windows isn't supported by the scripts.
- Python 3.10 or 3.11: `brew install python@3.11`
- ffmpeg: `brew install ffmpeg`
- About 4.5 GB of disk: 1.2 GB of Python libraries from `setup.sh`, plus about 3 GB of models that download the first time you use each tool (Seed-VC 2.3 GB, Whisper small about 0.5 GB, the rest smaller). The models are not part of this repo.

## Install

```bash
git clone https://github.com/heaversm/vocal-tools.git
cd vocal-tools
./setup.sh
```

`setup.sh` creates a Python environment in `.venv/` and downloads Seed-VC into `vendor/seed-vc/`, pinned to the version the included patch was written for.

## Run

On a Mac, open **Vocal Tools** from `~/Applications` (or press Cmd+Space and type Vocal Tools). It opens http://localhost:8010 in your browser. You can also double-click `start.command`.

The tools run as a background service: they start when you log in, keep running with no window open, and restart themselves if they crash. Bookmarking http://localhost:8010 works too. To manage the service, use `./service.sh`: `status`, `restart` (after changing `server.py`), `stop`, `uninstall`, or `log`. The log is `data/server.log`. If you move this folder, run `./make-app.sh` and `./service.sh install`.

On Linux, `./start.command` runs the tools in the terminal until you press Ctrl+C.

Every result is listed in the **Library** at the bottom of the page, with play, download, delete, and a button that opens its folder in Finder. Files are stored in `data/` inside this folder:

- `data/uploads/` holds files you drop on Convert and Sync. They're deleted as soon as the job ends.
- `data/results/` holds results. Anything older than 7 days is deleted when the app starts, so download what you want to keep. Change `KEEP_DAYS` in `server.py` to keep them longer.

## Speed

| Job | M2 Mac |
|---|---|
| Split a 30 s clip | about 8 s |
| Sync a 15 s take to a 30 s song | about 16 s |
| Pitch match the same | about 23 s |
| Tune the same | about 35 s |
| Change the key of a 30 s song, with instrumental | about 16 s |
| Split a 30 s song into four parts | about 14 s |
| Time the lyrics of a 30 s song | about 20 s |
| Convert a 30 s clip, 30 steps | about 10 min |
| Convert an 8 s clip, 10 steps | about 90 s |

Converting is much faster on an NVIDIA GPU. Fewer steps is faster and rougher.

## Tips

- Convert works best on clean, isolated vocals. Split a song first and use its vocals for both the performance and the target voice.
- Untick **Singing** for speech. Ticked, the output follows the performance's pitch exactly. Unticked, the pitch moves toward the target voice's natural range.
- Use **Shift semitones** when the target voice sits in a very different range, for example converting a male performance into a female voice.
- The first conversion of each kind is slow while the models download.
- Sync tells you if you sang in a different key, and flags a doubtful match (usually the wrong song). Tick **My recording has music in it** if you sang over a backing track, so the music is stripped first.
- `sync.py` and `pitch.py` also run from the command line: `.venv/bin/python pitch.py song.mp3 take.m4a out/`
- Pitch match compares against the song's separated vocal, so backing vocals and harmonies in the original can lower your score in those passages. A singer who deliberately takes a different melody scores low too.

## Changes to Seed-VC

`patches/seed-vc-apple-silicon.patch` changes three things in Seed-VC's `inference.py` so it runs on Apple GPUs. None of them affect NVIDIA or CPU machines:

- Pitch data is converted to 32-bit floats, because Apple GPUs can't handle 64-bit.
- The final audio stage (BigVGAN) runs on the CPU, because one of its operations is too large for Apple GPUs.
- Audio passed to that stage is moved to whichever device it runs on.

## Use responsibly

Only convert into the voice of someone who has agreed to it, and respect the copyright of the recordings you use. Seed-VC can produce convincing results.

## License

MIT for the code in this repo (see `LICENSE`). Seed-VC is GPL-3.0 and Demucs is MIT. Neither is included here: `setup.sh` downloads them.
