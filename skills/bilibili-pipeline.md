---
name: bilibili-pipeline
description: |
  Use when the user provides a Bilibili video URL and asks you to process, transcribe, summarize, or analyze it.
  This skill downloads audio (or CC subtitles if available), transcribes with faster-whisper, and summarizes via DeepSeek API.
  The pipeline script lives at ~/bilibili-pipeline/pipeline.sh.
  Triggers: bilibili, B站, b23.tv, BV1, 哔哩哔哩, 视频总结, 转录, 逐字稿
---

# Bilibili Video Pipeline

Pipeline in `~/bilibili-pipeline/`:

1. **Subtitle check**: yt-dlp tries CC subs first (`.json`/`.srt`/`.vtt`) — ~3s
2. **Audio download**: if no CC subs, yt-dlp pulls MP3 — ~10s
3. **Transcription**: faster-whisper (`base` model) transcribes the audio — ~30s-2min
4. **Summarization**: LLM structures the transcript into markdown — ~3-10s
5. **Visual analysis** (optional): `--analyze` flag triggers screenshot extraction + multimodal description

## Usage

```bash
cd ~/bilibili-pipeline

# Text-only (fast)
./pipeline.sh <bilibili-url>

# With screenshot analysis
./pipeline.sh --analyze <bilibili-url>

# Choose pipeline
./pipeline.sh --analyze --pipeline ds_qwen <bilibili-url>
```

## Output

```
output/<timestamp>/
├── audio.mp3              # (if no CC subs)
├── video.mp4              # (only with --analyze)
├── transcript.txt         # plain text transcript
├── transcript.srt         # SRT with timestamps
├── summary.md             # LLM structured summary
├── visual_notes.md        # (only with --analyze) frame descriptions
└── frames/                # (only with --analyze) extracted JPEGs
```

## Cost tracking

Each stage prints `[cost]` lines. All pricing in `config.json` (RMB ¥/1M tokens):

| Model | Input ¥/1M | Output ¥/1M |
|-------|-----------|-------------|
| DeepSeek V4 Flash | ¥1 | ¥2 |
| Qwen-VL-Plus | ¥0.8 | ¥2 |
| Agnes-2.0-Flash | **FREE** | **FREE** |
| DeepSeek V4 Pro | ¥5 | ¥10 |
| faster-whisper base | free (local) | — |

Typical cost for 5-min video with 12 frames (Agnes): **¥0** (free).  
With DS+Qwen: **~¥0.025**.

## Configuration (config.json)

All models, pricing, prompts, and download headers centralized in `config.json`:

- **providers**: API base URLs + env var names for API keys
- **download**: browser headers + video format for yt-dlp
- **prompts**: system prompts for visual_cue, frame_analysis, summarize, master_summarize
- **pipelines**: named pipeline configs (e.g. `agnes_full`, `ds_qwen`)
  - Each pipeline has: `visual_cue`, `frame_analysis`, optional `fallback`
  - Each step specifies: provider, model, temperature, max_tokens, pricing
- **summarize** / **judge**: top-level model configs

### Fallback mechanism

When `agnes_full` pipeline detects 0 visual cues, it falls back to `ds_qwen` (specified by `pipelines.agnes_full.fallback`). Both cue detection AND frame analysis use the fallback pipeline's config.

## Requirements

- API keys auto-loaded from `~/bilibili-pipeline/.env`:
  - `DEEPSEEK_API_KEY` — summarization, cue detection (fallback)
  - `DASHSCOPE_API_KEY` — Qwen frame analysis (fallback)
  - `AGNES_API_KEY` — default visual cue + frame analysis (free)
- Whisper model downloads on first use (~150MB for `base`)

## Visual analysis pipeline (analyze.py)

1. Parse `transcript.srt` — extract timestamped lines
2. LLM detects visual cue timestamps (triggers like "大家看", "如图", "屏幕")
3. If 0 cues found and `fallback` defined in config, auto-retry with fallback pipeline
4. `yt-dlp` downloads low-res video (cached)
5. `ffmpeg -ss` extracts JPEG frames at cue timestamps
6. Multimodal LLM describes each frame
7. Outputs `visual_notes.md` with inline markdown images + model info header

## Script locations

| File | Purpose |
|------|---------|
| `config.json` | Centralized model/prompt/pricing config |
| `config_get.py` | Read config values from bash |
| `pipeline.sh` | Orchestrator |
| `transcribe.py` | faster-whisper transcription |
| `summarize.py` | LLM summarization (reads config) |
| `analyze.py` | Visual analysis (reads config, fallback) |
| `batch_dl.py` | Batch download with anti-ban |
| `batch_up.py` | UP主批量采集 + master summary |
| `.env` | API keys (gitignored) |

## Batch download (batch_dl.py)

```bash
python3 batch_dl.py --url "https://..."
python3 batch_dl.py --urls-file ./list.txt
python3 batch_dl.py --playlist "https://..." --cookies ./cookies.txt
```

Anti-ban params overridable via `BATCH_DL_*` env vars.

## UP主批量采集 (batch_up.py)

```bash
python3 batch_up.py --up-uid 385474 --cookies ./cookies.txt
python3 batch_up.py --up-uid 385474 --cookies ./cookies.txt --max-videos 5
python3 batch_up.py --urls-file ./urls.txt
python3 batch_up.py --up-uid 385474 --cookies ./cookies.txt --resume
```

Bilibili 空间列表需要登录态 cookies。Create via Export Cookies browser extension.
