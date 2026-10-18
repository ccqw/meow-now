# Meow Now

Talk to a cat. Speech in, meows out.

Meow Now is a small, self-hosted web app with two experiences:

1. **Meow Mockery.** Talk for up to 60 seconds. A cat repeats it back to you:
   one meow for each syllable, in time with you, rising where your voice rose
   and falling where it fell. No words survive.
2. **Talkative Cats.** Four cats with opinions (grumpy, wise, anxious and a
   diva) each deliver a short monologue, entirely in meows, with English
   subtitles that light up word by word. Point it at a local
   [Ollama](https://ollama.com) model and the cats improvise; without one they
   use their own built-in lines.

Everything runs locally: no cloud APIs, no accounts, no per-minute costs, and
no audio is stored. Recordings are processed in memory and sent straight back.

## Quick start

With Docker (or Podman):

```sh
docker build -t meow-now .
docker run --rm -p 8080:8080 meow-now
```

or `docker compose up --build`, which also reads an optional `.env` file
(see [Configuration](#configuration)).

Then open <http://localhost:8080> and press **Start talking**.

From source (Python 3.11 or newer):

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
meow-now serve            # http://localhost:8080
```

There is also an offline converter, handy for testing with a recording:

```sh
meow-now mock hello.wav hello-meow.wav
```

> **Microphone note.** Browsers only allow microphone access on `https://`
> pages and on `localhost`. If you open the app from another machine over
> plain HTTP, use the **upload a recording** link instead, or put HTTPS in
> front of it (see [Deploying](#deploying)).

## Configuration

All settings are environment variables, and all are optional. Copy
`.env.example` to `.env` for Docker Compose.

| Variable | Default | What it does |
|---|---|---|
| `PORT` | `8080` | Port the web server listens on |
| `HOST` | `127.0.0.1` (`0.0.0.0` in the container image) | Address the web server binds to |
| `OLLAMA_URL` | unset | Base URL of an Ollama server, e.g. `http://your-ollama-host.internal:11434`. Unset means built-in monologues only |
| `OLLAMA_MODEL` | `llama3.2` | Ollama model the cats use |
| `MAX_UPLOAD_MB` | `8` | Largest recording the API accepts. 60 s of 16 kHz mono WAV is about 2 MB |
| `LOG_LEVEL` | `INFO` | Python log level |

If Ollama is unreachable, slow (over 20 s), or replies with something too
short, the cat quietly falls back to a built-in line. The page shows
"Freshly improvised." when a monologue came from the model.

## How it works

```
browser                                  server (FastAPI + numpy)
-------                                  ------------------------
MediaRecorder (webm/opus, mp4, ...)
  -> decodeAudioData + OfflineAudioContext
  -> 16 kHz mono 16-bit WAV  ---- POST /api/mockery ---->  analysis.py
                                                             energy envelope -> syllables
                                                             autocorrelation -> pitch + contour
                                                           mockery.py
                                                             voice pitch -> cat pitch
                                                           meows.py
                                                             one synthesized meow per syllable
  <audio> plays the result  <---- WAV (base64 JSON) -----
```

- **No ffmpeg on the server.** The browser decodes whatever it recorded and
  sends plain PCM WAV, so the server only needs Python's `wave` module and
  numpy. The image is a slim Python base with four pip dependencies.
- **Syllables** come from an RMS energy envelope (10 ms frames): loud runs are
  speech, short dips under 60 ms are bridged so a consonant does not split a
  syllable, and energy valleys split longer runs ("hello there" becomes four
  meows, not one).
- **Pitch** is a normalized autocorrelation per 40 ms frame, with parabolic
  peak interpolation, searched between 70 and 500 Hz. Each syllable keeps its
  median pitch and an 8-point contour.
- **Mapping to cat.** Your median pitch lands at 650 Hz, and each syllable
  keeps (and slightly exaggerates) how far above or below your median it was,
  clamped to 350 to 1200 Hz. Questions still go up.
- **Meows** are additive synthesis: a stack of harmonics on a rising and falling
  pitch track, each harmonic weighted by its distance to formants that glide
  through "m-ee-aa-oo", under an envelope that opens from a nasal "m". There
  are four kinds (`mew`, `meow`, `mrrp` with a rolled start, and a wavering
  `yowl`), chosen by duration. Synthesis is deterministic for a given seed.
- **Talkative cats** speak one meow per word, sized by word length and paced
  by the cat's tempo, with pauses at punctuation and rising contours on
  questions. The server returns each word's start time for the subtitles.

## API

Interactive docs are served at `/api/docs`.

| Method and path | Body / query | Returns |
|---|---|---|
| `GET /healthz` | | `{"status": "ok", "version": ...}` |
| `GET /api/cats` | | The cats, and whether Ollama is configured |
| `POST /api/mockery` | A PCM WAV file as the raw body (`Content-Type: audio/wav`), at least 8 kHz, any channels; only the first 60 s are used. Optional `?seed=` (0 or more) makes the result reproducible | `{"audio": "data:audio/wav;base64,...", "seed", "input_seconds", "truncated", "meows": [{start, duration, pitch_hz, your_pitch_hz, kind, contour, loudness}]}`. `contour` is the syllable's pitch over its median pitch through time (empty when unvoiced); `loudness` is 0 to 1, relative to the loudest syllable |
| `POST /api/cats/{id}/monologue` | `id` is `grumpy`, `wise`, `anxious`, `diva` or `random`; optional `?seed=` | `{"cat", "text", "source": "ollama" or "builtin", "words": [{word, start}], "audio"}` |
| `GET /api/meow` | `pitch` (200 to 1500 Hz), `duration` (0.08 to 2 s), `kind`, `seed` | One meow as `audio/wav` |

Examples:

```sh
curl -s -X POST --data-binary @hello.wav -H 'Content-Type: audio/wav' \
  http://localhost:8080/api/mockery | jq '.meows | length'

curl -s -o meow.wav 'http://localhost:8080/api/meow?pitch=800&duration=0.6&kind=mrrp'
```

Errors are JSON `{"detail": ...}` with status 400 (empty body), 404 (unknown
cat), 413 (too large), 415 (not a readable WAV, or below 8 kHz) or 422 (bad
query parameter).

## Deploying

The app is stateless: one small container, no volumes, no database, safe to
run with a read-only root filesystem and as many replicas as you like. It
needs about 100 MB of memory and a fraction of a CPU at rest.

### Kubernetes

[`deploy/kubernetes/meow-now.yaml`](deploy/kubernetes/meow-now.yaml) has a
Namespace, Deployment (non-root, read-only root filesystem, all capabilities
dropped, `/healthz` probes), Service and an example Ingress. Replace the
`IMAGE` and `HOSTNAME` placeholders, then:

```sh
kubectl apply -f deploy/kubernetes/meow-now.yaml
kubectl -n meow-now rollout status deploy/meow-now
```

Terminate TLS at your ingress so the microphone works.

### Container images

[`.github/workflows/publish-image.yml`](.github/workflows/publish-image.yml)
builds `linux/amd64` and `linux/arm64` images, each on its own native
GitHub-hosted runner (no emulation). On a pull request it builds both without
pushing and smoke-tests the image. On `main` it pushes
`ghcr.io/<owner>/<repo>:sha-<commit>`, and on a `v*.*.*` tag it pushes
`ghcr.io/<owner>/<repo>:<version>`. The run summary prints the full
`image:tag@digest` to pin in your manifests.

It logs in to GHCR with the workflow's own `GITHUB_TOKEN`, so a fork
publishes to its own GHCR with no secrets to set up. New GHCR packages start
private: to pull without credentials, set the package to public once in its
GitHub package settings.

## Development

```sh
pip install -e '.[dev]'
pytest            # unit and API tests
ruff check . && ruff format --check .
```

CI ([`.github/workflows/test.yml`](.github/workflows/test.yml)) runs the same
commands on Python 3.11, 3.12 and 3.13.

### Browser smoke test

`tests/browser` drives the real page in headless Chromium against a live local
server, with a fake microphone: it checks that the cats load, that recording
and stopping returns a mockery with audio and bars, and that a monologue plays
with subtitles. It needs Playwright and a browser, so plain `pytest` skips it.
To run it:

```sh
pip install -e '.[dev,browser]'
python -m playwright install chromium   # add --with-deps on Linux
pytest -v --browser-smoke tests/browser
```

Add `--headed` to watch it. CI runs it as a separate `browser` job.

Layout:

```
meow_now/
  audio.py      WAV decode/encode, resampling
  analysis.py   syllables, pitch and contour
  meows.py      meow synthesis and mixing
  mockery.py    Meow Mockery: speech -> meows
  cats.py       Talkative Cats: personalities, Ollama, cat speech
  web.py        FastAPI app
  static/       the single-page UI (no build step, no frameworks)
  __main__.py   `meow-now serve` and `meow-now mock`
tests/          pytest suite (synthetic speech, no fixtures on disk)
  browser/      headless Chromium smoke test (opt in with --browser-smoke)
deploy/         example Kubernetes manifests
```

## Phone line (not in this version)

Meow Now started as a phone tree: callers would press 1 or 2 on a real phone
number behind Asterisk. That version never ran end to end, and it needs a SIP
trunk and a phone number to test at all, so this rebuild ships the web app
and leaves telephony out. The core (`analysis`, `mockery`, `cats`) has no web
dependencies, so an Asterisk AGI adapter can sit on top of it later (tracked in #34).

## License

MIT. See [LICENSE](LICENSE).
