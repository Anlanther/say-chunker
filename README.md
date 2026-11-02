# say-chunker

Local, offline, long-form text-to-MP3 audiobook conversion for macOS on Apple
Silicon. It converts `.txt`, `.md`, and `.pdf` files (10,000+ characters,
potentially novel-length) into a single MP3 using the built-in macOS `say`
synthesis engine — no cloud APIs, no ML models, no downloads.

## How it works

The pipeline splits text into **sentence-counted chunks** and invokes `say` once
per chunk, strictly serially, with a configurable cooldown between calls. This
avoids `say` crashing on very large inputs and keeps the fanless MacBook Air from
overheating. Chunks are recorded as AIFF, then merged into a single MP3 with
`ffmpeg`.

```
.txt / .md / .pdf  →  extract  →  split sentences  →  chunk  →  say (per chunk)
                                                                       ↓
                     final MP3  ←  ffmpeg concat + loudnorm  ←  AIFF chunks
```

## Requirements

- macOS (Sonoma/Sequoia or newer) on Apple Silicon.
- Python 3.11+, managed **exclusively** with [`uv`](https://docs.astral.sh/uv/).
- `/usr/bin/say` (preinstalled on macOS).
- `ffmpeg` and `ffprobe` on `PATH` (`brew install ffmpeg`).

## Install

```bash
uv sync
```

This creates the project virtualenv and installs the `tts` entry point. All
commands below are run through `uv` (no bare `pip`, `pytest`, or `mypy`).

```bash
make install   # uv sync
make test      # uv run pytest
make lint      # uv run ruff check
make typecheck # uv run mypy --strict say_chunker
make run       # uv run tts --help
```

## First run

```bash
# See what's available
uv run tts voices

# Dry-run: extractor, section count, sentence count, chunk count
uv run tts inspect book.pdf
uv run tts inspect --sentences-per-chunk 8 book.pdf

# Convert a PDF to MP3
uv run tts convert book.pdf -o book.mp3

# Convert markdown or plain text
uv run tts convert notes.md
uv run tts convert novel.txt --voice Alex --rate 220

# From stdin (format is required for stdin)
cat chapter.txt | uv run tts convert - --format txt -o chapter.mp3
```

The default output path is `<title>.mp3` in the current directory (the title
comes from PDF metadata or the first Markdown `H1`, falling back to the filename).

## Voice selection

`uv run tts voices` lists every voice installed on the machine. Pick one with
`--voice`:

```bash
uv run tts convert book.pdf --voice Daniel --rate 185
```

The default is `Samantha` at 200 words per minute. The chosen voice is validated
at startup against `say -v '?'`; if it is missing you get an error listing the
valid voices.

## PDF caveats

- Extraction is **layout-aware**: reading order is preserved, repeated
  headers/footers and standalone page numbers are stripped, and hyphenated
  line-wraps (`exam-\nple` → `example`) are collapsed.
- PDF **TOC/bookmarks** are used as hard section boundaries, so a chapter ending
  with two sentences produces a two-sentence chunk rather than being merged with
  the next chapter.
- **Password-protected** PDFs: pass `--pdf-password`.
- **Scanned** PDFs (pages with no extractable text) are not silently skipped; a
  warning lists the affected page numbers and suggests running `ocrmypdf` first.
  OCR is intentionally not bundled.

## Resume semantics

Every run persists job state under `~/.local/state/say_chunker/jobs/<job_id>.json`
and writes a log to `<job_id>.log`. The **job id is a stable hash of**:

```
(content_hash, voice, rate, sentences_per_chunk, language)
```

- Change any of those five and you get a **new job id** (chunk boundaries depend
  on sentence count, so the state is not reused).
- On resume, the current config is validated against the stored config; a
  mismatch fails with a clear message.
- Completed chunks are skipped, so recovering from a crash or `Ctrl-C` only
  synthesizes the remaining chunks and re-merges.

```bash
# After an interruption or failure:
uv run tts resume <job_id>

# Convert with an automatic resume hint if a matching job already exists:
uv run tts convert book.pdf -o book.mp3 --resume

# Inspect job state
uv run tts list-jobs

# Remove old jobs / temp files
uv run tts clean --older-than 7d
```

Resuming a **completed** job whose output MP3 still exists is a no-op (no audio
is duplicated). A fresh `tts convert` (without `--resume`) always starts over and
overwrites the output.

## Tuning

- `--sentences-per-chunk N` (default `5`) controls how many sentences go into a
  single `say` call. Lower values are safer for very long sentences but produce
  more subprocesses and slightly more abrupt boundaries. Higher values are faster
  but risk long individual `say` calls.
- `--sleep-between-chunks` / `--sleep` (default `0.5`) is the cooldown between
  chunks. `say` is lightweight, so the default is short; raise it if you notice
  thermal throttling or audio glitches.
- `--max-sentence-chars` (default `2000`) is the safety net: a single
  pathological sentence longer than this is split at clause boundaries
  (commas/semicolons/em-dashes/conjunctions) with a logged warning, never
  truncated.
- `--retries` (default `2`) retries a failed `say` call with exponential backoff
  before pausing the job.
- `--max-runtime 2h` caps total runtime; when hit, the job pauses cleanly and can
  be resumed.
- `--no-normalize` disables `loudnorm` (normalization is on by default).

## Configuration precedence

Settings resolve as **CLI flags > `TTS_*` environment variables >
`~/.config/say_chunker/config.toml` > defaults**.

```toml
# ~/.config/say_chunker/config.toml
voice = "Daniel"
rate = 185
sentences_per_chunk = 6
sleep_between_chunks = 0.75
```

Environment variables use the `TTS_` prefix (e.g. `TTS_RATE=185`,
`TTS_VOICE=Daniel`, `TTS_NORMALIZE=false`).

## Layout

```
say_chunker/
  cli.py                      # typer entrypoint + composition root
  config.py                   # pydantic-settings config
  domain/
    models.py                 # Sentence, Section, SourceDocument, Chunk, ...
    ports.py                  # TTSEngine, TextExtractor, SentenceSplitter, ...
    errors.py                 # exception hierarchy
  adapters/
    say_engine.py             # TTSEngine via subprocess
    pysbd_splitter.py         # SentenceSplitter via pysbd
    ffmpeg_merger.py          # AudioMerger (concat + encode MP3)
    json_job_store.py         # JobStore (resume support)
    extractors/               # txt / md / pdf + registry
  pipeline/
    orchestrator.py           # extract -> split -> chunk -> synth -> merge
    chunker.py                # sentence-counted chunking
    throttle.py               # inter-chunk cooldown
  utils/
    logging.py
    subprocess_helpers.py     # timeouts, cleanup, structured errors
    textutil.py
```

## Development notes

- The orchestrator depends only on the `domain.ports` protocols; concrete
  adapters are injected at the CLI boundary, so future engines (Piper, Kokoro,
  `edge-tts`) can be added without touching the pipeline.
- `mypy --strict` and `ruff` are clean; `pytest` covers the splitter, chunker,
  all three extractors, the `say`/`ffmpeg` adapters (via fake binaries), and the
  full pipeline + resume flow with a mocked TTS engine.
