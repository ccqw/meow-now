# Meow Now

Talk to a cat. Speech in, meows out.

Meow Now is a small, self-hosted web app with three experiences:

1. **Meow Mockery.** Talk for up to 60 seconds. A cat repeats it back to you:
   one meow for each syllable, in time with you, rising where your voice rose
   and falling where it fell. No words survive. It also reacts: say nothing
   and it waits you out, then asks; end on a question and it answers with a
   flat "meh"; talk past about 40 seconds and it loses interest, then yawns.
2. **Talkative Cats.** Four cats with opinions (grumpy, wise, anxious and a
   diva) each deliver a short monologue, entirely in meows, with English
   subtitles that light up word by word. Point it at a local
   [Ollama](https://ollama.com) model and the cats improvise; without one they
   use their own built-in lines. Or type a line yourself and the cat on stage
   says it, in its own voice.
3. **Copycat.** A call and response game. The cat on stage sings a short
   tune of meows; you sing it back. It scores how well your copy matched
   (the count, the timing and the tune), tells you meow by meow what was off,
   and each win adds one more meow. Your best run is kept in your browser,
   and nothing else.

Every result can be saved as a WAV (named for the cat) or shared from the
deck under the stage. A built-in monologue can also travel as a link:
`/?cat=<id>&seed=<n>` opens the page straight into that exact performance,
same line, same audio, same subtitles, waiting for you to press play
(browsers do not start sound on their own). The link carries only the cat
and the seed; an improvised or typed line is never turned into a link,
because that would mean storing it.

Everything runs locally: no cloud APIs, no accounts, no per-minute costs, and
no audio or text is stored. Recordings and typed lines are processed in memory
and sent straight back.

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
| `RATE_LIMIT_PER_MIN` | `30` | Requests per minute each client may make to the synthesis endpoints (`POST /api/mockery`, `POST /api/cats/{id}/monologue`, `POST /api/speak` and both Copycat endpoints), with bursts up to the same number. Over it the API answers 429 with `Retry-After`. `0` turns the limit off |
| `TRUST_PROXY_HEADERS` | `false` | Set to `true` behind a reverse proxy you run, so the rate limit keys on the last `X-Forwarded-For` address (the one your proxy saw) instead of the proxy's own address. Leave it off when clients connect directly, or they can pick their own key |
| `MAX_CONCURRENT_RENDERS` | `4` | Syntheses a replica runs at once. Further requests wait for a slot (see the next row), then get 503 with `Retry-After`. `0` means no cap |
| `RENDER_WAIT_S` | `5` | Seconds a request waits for a free synthesis slot before the 503 |
| `LOG_LEVEL` | `INFO` | Python log level |

`meow-now serve` logs one JSON object per line. Each request gets one line
with its method, path (no query string), status and `duration_ms`. Client
addresses, query strings and bodies are never logged, so no recording leaves
a trace. Every replica keeps its own rate-limit and concurrency counts in
memory, so the effective limit grows with the number of replicas.

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
  <audio> plays the result  <---- 24 kHz WAV (base64 JSON)
```

- **No ffmpeg on the server.** The browser decodes whatever it recorded and
  sends plain PCM WAV, so the server only needs Python's `wave` module and
  numpy. The image is a slim Python base with four pip dependencies.
- **Syllables** come from an RMS energy envelope (10 ms frames): loud runs are
  speech, short dips under 60 ms are bridged so a consonant does not split a
  syllable, and energy valleys split longer runs ("hello there" becomes four
  meows, not one).
- **Pitch** is a normalized autocorrelation per 40 ms frame, with parabolic
  peak interpolation, searched between 70 and 500 Hz (1300 Hz for Copycat).
  The search starts past the autocorrelation's first zero crossing, so a low
  voice is not mistaken for its own lag-0 lobe, and half the lag wins when it
  correlates nearly as well, so a high voice is not heard an octave low. Each
  syllable keeps its median pitch and an 8-point contour.
- **Mapping to cat.** Your median pitch lands at 650 Hz, and each syllable
  keeps (and slightly exaggerates) how far above or below your median it was,
  clamped to 350 to 1200 Hz. Questions still go up.
- **Reactions.** A recording with no syllables comes back as its own silence
  plus one small rising `mrrp` (`silent`). If you talk past 40 s, syllables
  after that get shorter and quieter over the next 20 s, and the result ends
  in one long falling yawn (`bored`). Otherwise, if the last syllable lasts at
  least 0.15 s and its contour ends at least 12% above its first half, the cat
  mirrors the question, pauses 0.6 s and adds one flat, falling meow
  (`question`). Only one reaction applies, in that order of priority.
- **Meows** are source-filter synthesis at 24 kHz (recordings are analyzed at
  16 kHz, which is plenty for speech pitch). The source is a stack of
  harmonics on a rising and falling pitch track, with about 1% pitch jitter,
  amplitude shimmer and, on meows over 0.4 s, a 5 to 6 Hz vibrato of random
  depth. Each harmonic is weighted by five formants that glide through
  "m-ee-aa-oo", with a nasal anti-formant on the opening "m". Breath noise,
  pulsed with each glottal cycle, goes through the same formants: loudest on
  the attack and release, with a little throughout. There are four kinds of
  meow (`mew`, `meow`, `mrrp` with a rolled start, and a wavering `yowl`),
  chosen by duration. Synthesis is deterministic for a given seed.
- **Purrs** are 20 to 30 Hz laryngeal pulses rung through the closed-mouth
  formants, so the purr carries energy at a few hundred Hz to a few kHz and
  reads even on a phone speaker. They rise and fall with a slow in-and-out
  breath.
- **Talkative cats** speak one meow per word, sized by word length and paced
  by the cat's tempo, with pauses at punctuation and rising contours on
  questions. The server returns each word's start time for the subtitles.
  Each cat also has its own voice: Mittens is a big tom with a low, growly
  voice (lower formants and subharmonics) who grumbles in `mrrp`s; Professor
  Whiskers is airy and given to the odd purr; Biscuit is small, breathy and
  high, and mews; the Duchess is clear and bright with an operatic vibrato,
  and yowls.

### Copycat scoring

A phrase is drawn from a seed: meows of 0.2 to 0.5 s, mostly quick 0.2 s
gaps with the odd 0.55 s beat, every step at least 3 semitones from the
last, each meow plain, rising, falling or arched. A longer phrase always
starts with the shorter one. The chosen cat performs it in its own voice
(Mittens' growl tamed so the tune stays clear), with the top note kept at
950 Hz or below, where a 16 kHz recording still tracks reliably.

`copycat.py` scores your copy with `analysis.analyze` (its pitch ceiling
raised to 1300 Hz, for cats and for people doing cat voices):

- **Count.** Your syllables are matched to the cat's meows by a dynamic
  program. A meow left over is *missed*, a syllable left over is an *extra*.
  Touching pieces of one sound may match one meow together.
- **Timing.** One global offset and one tempo (up to 1.5 times faster or
  slower) are fitted to your onsets, so you can start when you like; each
  meow is then *early* or *late* by what is left.
- **Pitch.** In semitones, after removing your own transposition (the median
  over your meows), so you can sing it in any register. A note a whole
  octave out counts as right. Each meow's contour is compared after removing
  its mean and allowing you to exaggerate or flatten it up to twice.

Each meow gets a weighted geometric mean of its three marks (timing 0.3,
pitch 0.45, shape 0.25), so good timing cannot buy back a wrong note. The
score is the mean over the phrase's meows, less half a meow for each extra.
A round is won at 75 or better with every meow matched and nothing extra.

The tolerances were tuned on simulated copies, not guessed. Each one sits
near the 98th percentile of that error over honest copies: early or late
past 0.1 s, higher or lower past 2.5 semitones, the wrong shape past 1.4
semitones RMS. Scores over 300 simulated copies per case (`python
tests/test_copycat.py 300` prints this table again):

| Copy | 5th percentile | Median | 95th percentile | Passed |
|---|---|---|---|---|
| Honest: a person meowing it back, an octave or so down, own tempo, onsets within 40 ms and notes within a semitone (standard deviations) | 86 | 93 | 99 | 100% |
| Honest, in a room (reverb 10 dB under the voice, mic hiss) | 81 | 91 | 99 | 98% |
| Sloppy: onsets within 80 ms, notes within 1.8 semitones | 64 | 81 | 97 | 80% |
| Flat monotone, right rhythm | 34 | 53 | 81 | 9% |
| Right rhythm, wrong tune | 15 | 56 | 86 | 16% |
| Another phrase of the same length | 16 | 43 | 83 | 11% |
| A meow missing | 41 | 74 | 87 | 0% |
| A meow extra | 49 | 87 | 93 | 0% |

The phrase's own plan, sung again with fresh jitter and breath, scores 97 or
more; the same copy shifted 12, 7 or 5 semitones down or 2 up scores 87 or
more. Silence scores 0. Two meows carry little information, so most wrong
copies that pass are two-meow rounds; a mistimed meow is hard to tell from a
different tempo when it is the last of three. Wrong counts never pass.

## API

Interactive docs are served at `/api/docs`.

| Method and path | Body / query | Returns |
|---|---|---|
| `GET /healthz` | | `{"status": "ok", "version": ...}` |
| `GET /api/cats` | | The cats, and whether Ollama is configured |
| `POST /api/mockery` | A PCM WAV file as the raw body (`Content-Type: audio/wav`), at least 8 kHz, any channels; only the first 60 s are used. Optional `?seed=` (0 or more) makes the result reproducible. Optional `?cat=` (a cat id or `random`) mocks you in that cat's voice, centred on its pitch; an unknown id is 404 | `{"audio": "data:audio/wav;base64,...", "seed", "cat": {id, name, tagline} or null, "input_seconds", "truncated", "meows": [{start, duration, pitch_hz, your_pitch_hz, kind, contour, loudness}]}`. `contour` is the syllable's pitch over its median pitch through time (empty when unvoiced); `loudness` is 0 to 1, relative to the loudest syllable. Also `"reaction"`: `none`, `silent`, `question` or `bored`; `"caption"`: a short line about the reaction for display, or `null` for `none`; and `"reaction_meows": [{start, duration, pitch_hz, kind}]`, the extra meows the reaction added (not tied to a syllable). A silent recording is not an error: it returns 200 with `reaction: "silent"` and no `meows` |
| `POST /api/cats/{id}/monologue` | `id` is `grumpy`, `wise`, `anxious`, `diva` or `random`; optional `?seed=`; `?improvise=false` always uses a built-in line, even with Ollama configured | `{"cat", "text", "source": "ollama" or "builtin", "seed", "words": [{word, start, duration, kind}], "audio"}`. Each word's `start` and `duration` are in seconds and `kind` is the meow kind rendered for it. A built-in line is fixed by the cat and the seed, which is what a shared link replays |
| `POST /api/speak` | JSON `{"text": ..., "cat": ...}`; `cat` is an id or `random` (the default); `text` is up to 1000 characters; optional `?seed=` | The same shape as a monologue, with `"source": "typed"` and `"truncated"`. Control and format characters are dropped, whitespace is collapsed, and the line is capped at 45 words, the monologue limit. Empty text (after cleaning) is a 422. Nothing is stored or logged |
| `GET /api/copycat/phrase` | `length` (1 to 12, required); optional `seed` (0 or more; random if left out) and `cat` (an id or `random`) | `{"seed", "length", "cat", "duration", "pass_score", "meows": [{start, duration, pitch_hz, kind, contour}], "audio"}`. `pitch_hz` is each meow's median pitch as heard and `contour` its pitch over that median at 8 even steps. Deterministic per seed, length and cat; a longer phrase starts with the shorter one |
| `POST /api/copycat/score` | Your copy as a PCM WAV body (as for the mockery; up to 30 s are used), with the phrase's `seed` and `length` (both required) and its `cat` | `{"score": 0 to 100, "passed", "pass_score", "extra", "heard", "tempo", "transpose", "meows": [{verdict, score, onset_error, pitch_error, shape_error, heard}]}`, one entry per meow of the phrase. `verdict` is `good`, `higher`, `lower`, `early`, `late`, `shape`, `unvoiced` or `missed`; errors are in seconds (positive is late) and semitones (positive is higher); `heard` is what was heard for that meow, mapped onto the phrase's timeline and into the cat's register (`{start, duration, pitch_hz, contour}`), or `null` if missed. Deterministic for the same recording |
| `GET /api/meow` | `pitch` (200 to 1500 Hz), `duration` (0.08 to 2 s), `kind` (`mew`, `meow`, `mrrp`, `yowl` or `purr`), `seed`. For a purr, `pitch` sets the pulse rate within 20 to 30 Hz (600 gives 25 Hz) | One meow (or purr) as `audio/wav` |

Examples:

```sh
curl -s -X POST --data-binary @hello.wav -H 'Content-Type: audio/wav' \
  http://localhost:8080/api/mockery | jq '.meows | length'

curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"text": "Is it dinner yet?", "cat": "grumpy"}' \
  http://localhost:8080/api/speak | jq -r '.words[].word'

curl -s -o meow.wav 'http://localhost:8080/api/meow?pitch=800&duration=0.6&kind=mrrp'
curl -s -o purr.wav 'http://localhost:8080/api/meow?duration=2&kind=purr'
```

All audio the server returns is 24 kHz mono 16-bit WAV.

Errors are JSON `{"detail": ...}` with status 400 (empty body), 404 (unknown
cat), 413 (too large), 415 (not a readable WAV, or below 8 kHz), 422 (bad
query parameter or body, or nothing to say), 429 (over the rate limit) or 503 (every synthesis slot
busy). 429 and 503 carry a `Retry-After` header in seconds.

Every response carries security headers: a Content Security Policy,
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
`Permissions-Policy: microphone=(self)`, `X-Frame-Options: DENY` and
`frame-ancestors 'none'`. The page keeps its script and style inline, so
instead of `'unsafe-inline'` or per-block hashes (which would change with
every edit to the page) the server stamps a fresh nonce on its `<script>` and
`<style>` tags for each response and allows only that nonce. The policy also
allows Google Fonts and `data:`/`blob:` audio. The `/api/docs` page loads
Swagger UI from its CDN and gets a separate, looser policy.

## Deploying

The app is stateless: one small container, no volumes, no database, safe to
run with a read-only root filesystem and as many replicas as you like. It
needs about 100 MB of memory and a fraction of a CPU at rest.

### Kubernetes

[`deploy/kubernetes/meow-now.yaml`](deploy/kubernetes/meow-now.yaml) has a
Namespace, Deployment (non-root, read-only root filesystem, all capabilities
dropped, `/healthz` probes) with 2 replicas, a PodDisruptionBudget, an
example HorizontalPodAutoscaler (70% CPU, needs a metrics server), a Service
and an example Ingress. The example Ingress forwards the client address, so
the Deployment sets `TRUST_PROXY_HEADERS=true`; if clients can reach the pods
without going through your proxy, set it back to `false`. Replace the `IMAGE`
and `HOSTNAME` placeholders, then:

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
and stopping returns a mockery with audio and bars in the chosen cat's voice,
that a silent recording shows the cat's caption, that a monologue plays with
subtitles, that Save downloads a WAV named for the cat, that a copied link
reopens the same monologue waiting for play (and an unknown cat falls back to
the cast), that a typed line is performed and shared as a file, and that
Copycat plays a round from the keyboard with the fake microphone, keeps your
best across a reload (and still plays when storage is refused), all
without a single Content Security Policy violation (and that the policy
really blocks an inline script without the nonce). It needs Playwright and a
browser, so plain `pytest` skips it.
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
  meows.py      meow and purr synthesis, and mixing
  mockery.py    Meow Mockery: speech -> meows
  cats.py       Talkative Cats: personalities, Ollama, cat speech
  web.py        FastAPI app
  limits.py     per-client rate limit and synthesis concurrency cap
  middleware.py security headers, JSON access log
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
