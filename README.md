# say-chunker

Local, offline, long-form text-to-MP3 audiobook conversion for macOS on Apple
Silicon. It turns `.txt`, `.md`, and `.pdf` files into a single MP3 using the
built-in macOS `say` engine — no cloud APIs, no ML models, and no model
downloads.

The pipeline splits text into **sentence-counted chunks** and invokes `say` once
per chunk, strictly serially, with a configurable cooldown between calls. That
avoids `say` crashing on very large inputs and keeps a fanless MacBook Air from
overheating. Chunks are written as AIFF, then merged into one MP3 with `ffmpeg`.

```
.txt / .md / .pdf  →  extract  →  split sentences  →  chunk  →  say (per chunk)
                                                                       ↓
                     final MP3  ←  ffmpeg concat + loudnorm  ←  AIFF chunks
```

## Requirements

- macOS (Sonoma/Sequoia or newer) on Apple Silicon (tested target: M1, 16 GB).
- Python 3.11+, managed **exclusively** with [`uv`](https://docs.astral.sh/uv/).
- `/usr/bin/say` (preinstalled on macOS).
- `ffmpeg` and `ffprobe` on `PATH` (`brew install ffmpeg`).

`uv` is the only supported package/venv tool. There is no `pip`, `poetry`,
`conda`, or bare `virtualenv` workflow.

## Install

```bash
uv sync
```

This creates the project virtualenv, installs the `tts` entry point, and writes
`uv.lock`. Every command below goes through `uv`:

```bash
make install    # uv sync
make test       # uv run pytest
make lint       # uv run ruff check .
make typecheck  # uv run mypy --strict say_chunker tests
make format     # uv run ruff format .
make run        # uv run tts --help
```

`.python-version` pins Python 3.13 for the development environment;
`requires-python = ">=3.11"` keeps the package installable on 3.11+.

The Python package is `say_chunker` (distributed as the `say-chunker` project,
exposed as the `tts` command). Job state and config live under
`~/.local/state/say_chunker` and `~/.config/say_chunker`.

## First run

```bash
# See what's available
uv run tts voices

# Dry-run: extractor, section count, sentence count, chunk count, warnings
uv run tts inspect book.pdf
uv run tts inspect --sentences-per-chunk 8 book.pdf

# Convert a PDF to MP3
uv run tts convert book.pdf -o book.mp3

# Convert Markdown or plain text
uv run tts convert notes.md
uv run tts convert novel.txt --voice Alex --rate 220

# From stdin (--format is required for stdin)
cat chapter.txt | uv run tts convert - --format txt -o chapter.mp3
```

The default output path is `<title>.mp3` in the current directory. The title
comes from PDF metadata, the first Markdown `H1`, or the filename stem. For
stdin, the fallback title is `stdin`.

`inspect` is a pure extraction/planning dry run: it does not create a job, call
`say`, or invoke `ffmpeg`. It is safe to run before committing to a multi-hour
conversion. `convert` and `resume` validate `say`, `ffmpeg`, `ffprobe`, and the
selected voice before starting a long run.

## Voice selection

`uv run tts voices` lists every voice installed on the machine. Pick one with
`--voice`:

```bash
uv run tts convert book.pdf --voice Daniel --rate 185
```

The default is `Samantha` at 200 words per minute. The chosen voice is validated
at startup by parsing `say -v '?'`; if it is missing, the command fails with a
helpful list of installed voices. Voice names with spaces (for example
`Bad News`) are supported.

## PDF handling

- Extraction uses **PyMuPDF 1.x** (`pymupdf`), pinned below 2.0 so the package
  does not pull in ML layout/ONNX dependencies.
- Pages are read in reading order. Repeated headers/footers near page edges and
  standalone page numbers are stripped.
- Paragraph breaks are preserved and hyphenated line-wraps are collapsed
  (`exam-\nple` → `example`).
- PDF **TOC/bookmarks** are used as hard section boundaries. A chapter that ends
  with two sentences produces a two-sentence chunk rather than being merged with
  the next chapter.
- **Password-protected** PDFs: pass `--pdf-password`, or let the CLI prompt for
  the password interactively.
- **Scanned** PDFs (pages with no extractable text) are not silently skipped. A
  warning lists affected page numbers and suggests running `ocrmypdf` first.
  OCR is intentionally not bundled.

## Resume semantics

Every run persists job state under:

```
~/.local/state/say_chunker/jobs/<job_id>.json
```

and logs under:

```
~/.local/state/say_chunker/jobs/<job_id>.log
```

The **job id is a stable 16-hex-character hash of**:

```
(content_hash, voice, rate, sentences_per_chunk, language)
```

- Changing any of those five produces a **new job id** and cannot collide with
  the old resume state.
- On `convert --resume`, the extractor and plan signature are validated against
  the stored job. `max_sentence_chars` is part of that signature; a mismatch
  fails with a clear message. Runtime/merge settings (`--sleep`, `--retries`,
  `--normalize`, bitrate) may change between attempts.
- `tts resume <job_id>` uses the job's **stored** configuration, so a later
  config-file/env change does not silently alter the plan.
- Completed chunks are skipped. If a chunk audio file is missing, it is
  re-synthesized.
- Resuming a completed job whose MP3 still exists is a no-op, so running the
  same command twice does not duplicate audio.
- A fresh `tts convert` (without `--resume`) starts over and overwrites the
  stored job state and the final MP3.
- If the output MP3 was deleted after a successful job, `tts resume` regenerates
  it (the cleaned work files are re-synthesized).

```bash
# After an interruption, failure, or Ctrl-C:
uv run tts resume <job_id>

# Resume a matching job if one exists, otherwise start fresh:
uv run tts convert book.pdf -o book.mp3 --resume

# Inspect job state:
uv run tts list-jobs

# Remove old job state, logs, and temporary audio:
uv run tts clean --older-than 7d
```

Ctrl-C is handled gracefully: the current `say` chunk is allowed to finish, the
job is persisted as paused, and the process exits with a resume hint. The `say`
subprocess runs in its own session so the terminal signal does not kill it
mid-chunk. A second Ctrl-C is ignored while the current chunk finishes.

## Tuning

- `--sentences-per-chunk N` (default `5`) caps how many sentence units go into a
  single `say` invocation. Lower values are safer for very long sentences but
  create more subprocesses and more chunk boundaries. Higher values are faster
  but make individual `say` calls longer.
- `--sleep-between-chunks` / `--sleep` (default `0.5`) is the cooldown between
  calls. `say` is lightweight; raise this if you notice thermal throttling or
  audio glitches.
- `--max-sentence-chars` (default `2000`) is the safety net: a single
  pathological sentence longer than this is split at commas, semicolons,
  em-dashes, and conjunctions (falling back to word boundaries) with a logged
  warning. Text is never truncated.
- `--retries` (default `2`) retries a failed `say` call with exponential backoff
  (1s, 2s, …) before pausing the job.
- `--timeout-seconds` (default `300`) is the per-chunk `say` subprocess timeout.
  On timeout the process group is killed and the retry logic takes over.
- `--max-runtime 2h` caps a single invocation's runtime. When hit, the job
  pauses cleanly and can be resumed.
- `--no-normalize` disables `loudnorm` (single-pass normalization is on by
  default).
- `--bitrate-kbps` selects the MP3 bitrate (128–192, default 128). A 250 ms
  silence is inserted between chunks at merge time; configure it with
  `TTS_SILENCE_BETWEEN_CHUNKS_MS`. The merged file is verified with `ffprobe`
  before it is atomically moved into place.

## Configuration precedence

Settings resolve as **CLI flags > `TTS_*` environment variables >
`~/.config/say_chunker/config.toml` > defaults**, via `pydantic-settings`.

```toml
# ~/.config/say_chunker/config.toml
voice = "Daniel"
rate = 185
sentences_per_chunk = 6
sleep_between_chunks = 0.75
normalize = true
max_runtime_seconds = "2h"   # seconds or a duration string
# say_binary = "/usr/bin/say"
# ffmpeg_binary = "ffmpeg"
# state_dir = "/custom/state/say_chunker"
```

Environment variables use the `TTS_` prefix:

```bash
TTS_RATE=185 TTS_VOICE=Daniel TTS_NORMALIZE=false uv run tts convert book.pdf
TTS_STATE_DIR=/tmp/tts-state uv run tts list-jobs
TTS_CONFIG_FILE=/path/to/config.toml uv run tts inspect book.pdf
```

## Design and layout

```
say_chunker/
  cli.py                      # typer entrypoint + composition root
  config.py                   # pydantic-settings config
  domain/
    models.py                 # Sentence, Section, Chunk, SourceDocument, ...
    ports.py                  # TTSEngine, TextExtractor, SentenceSplitter, ...
    errors.py                 # user-facing exception hierarchy
  adapters/
    say_engine.py             # TTSEngine via subprocess (-f tempfile, -o AIFF)
    pysbd_splitter.py         # SentenceSplitter via pysbd + CJK/regex fallback
    ffmpeg_merger.py          # AudioMerger: concat demuxer, silence, loudnorm
    json_job_store.py         # JobStore: atomic JSON state, resume, cleanup
    extractors/
      __init__.py             # extension/format registry
      txt_extractor.py
      markdown_extractor.py
      pdf_extractor.py
  pipeline/
    orchestrator.py           # extract -> split -> chunk -> synth -> merge
    chunker.py                # sentence-counted chunking + long-sentence net
    throttle.py               # inter-chunk cooldown
  utils/
    logging.py
    subprocess_helpers.py     # timeouts, process-group cleanup, errors
    textutil.py
```

Clean layering is enforced by construction:

- The orchestrator imports only `domain.ports` protocols and pure domain/utility
  types, never adapters.
- Concrete adapters (including the extractor chosen for the input extension) are
  built at the `cli.py` boundary and injected.
- The domain layer has no pydantic, typer, subprocess, or adapter imports; it
  uses `pathlib` only for path types.
- Library code logs; only the CLI prints user-facing progress.

Future engines (Piper, Kokoro, `edge-tts`) can implement `TTSEngine` without
touching the pipeline. The CLI-first design leaves room for a minimal GUI later.

## Chunking rules

1. `pysbd` splits each section into sentences with a paragraph index. Abbreviations,
   initials, decimals, ellipses, and quoted speech are handled. Unsupported
   languages and CJK text that `pysbd` leaves as one sentence fall back to a
   deterministic regex splitter with a warning.
2. The chunker groups consecutive sentences up to `--sentences-per-chunk`.
3. It closes a chunk early at a paragraph break.
4. Sections are chunked independently, so section boundaries are hard
   boundaries. A section ending with two sentences produces a two-sentence
   chunk.
5. A final shorter chunk is normal. Chunks never contain zero sentences or only
   whitespace.
6. Long sentences are split at clause boundaries and count as multiple speakable
   units; the warning is logged and no text is discarded.
7. Given identical input and configuration, chunk boundaries are identical
   across runs. This is required for resume correctness.

## Development

```bash
uv sync
uv run pytest
uv run mypy --strict say_chunker tests
uv run ruff check .
uv run ruff format .
```

Fixtures live in `tests/fixtures/`:

- `long_sample.txt` — 12k+ characters with mixed paragraphs.
- `sample.md` — headings, lists, emphasis, links, inline code, a code block.
- `sample.pdf` — 6 pages with repeated headers/footers, page numbers, a
  hyphenated line-wrap, metadata title, and a two-chapter TOC.

Regenerate them with:

```bash
uv run python tests/fixtures/build_fixtures.py
```

Tests cover the splitter (abbreviations, decimals, ellipses, quotes, CJK
fallback, no punctuation), the chunker (multiples, tails, paragraph/section
boundaries, long-sentence splitting, empty filtering, determinism), all three
extractors, the `say` and `ffmpeg` adapters (via fake binaries), the JSON job
store, and the full pipeline + resume flow with a mock TTS engine.

## Limitations

- macOS only: synthesis is `/usr/bin/say`.
- No OCR. Run `ocrmypdf` before converting scanned PDFs.
- No GUI yet; the CLI is the supported interface.
- `say` output quality and available voices depend on the macOS installation.
- Very large books can take hours; `say` runs serially by design.
