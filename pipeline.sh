#!/bin/bash
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 [--analyze] <bilibili-url>"
    echo "Example: $0 --analyze https://www.bilibili.com/video/BV1xx411c7mD"
    exit 1
fi

ANALYZE=""
PIPELINE="agnes_full"
while [ "${1#--}" != "$1" ]; do
    case "$1" in
        --analyze) ANALYZE="1"; shift ;;
        --pipeline) PIPELINE="$2"; shift 2 ;;
        *) break ;;
    esac
done

URL="$1"
DIR="$(cd "$(dirname "$0")" && pwd)"
[ -f "$DIR/.env" ] && set -a && source "$DIR/.env" && set +a

# === Extract BV from URL ===
BV=""
if [[ "$URL" =~ BV[a-zA-Z0-9]{10,} ]]; then
    BV="${BASH_REMATCH[0]}"
fi

# === Resolve output base from config ===
get_config() { python3 "$DIR/config_get.py" "$1" 2>/dev/null; }
OUTPUT_BASE="$(get_config output_dir)"
OUTPUT_BASE="${OUTPUT_BASE:-~/Documents/bilibili}"
OUTPUT_BASE="${OUTPUT_BASE/#\~/$HOME}"

OUTDIR="$OUTPUT_BASE/clips/${BV}$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTDIR"

# === BV-prefixed filenames ===
PREFIX="${BV}_"
TXTFILE="$OUTDIR/${PREFIX}transcript.txt"
SRTFILE="$OUTDIR/${PREFIX}transcript.srt"
SUMMARYFILE="$OUTDIR/${PREFIX}summary.md"
VIDEOFILE="$OUTDIR/${PREFIX}video.mp4"
NOTEFILE="$OUTDIR/${PREFIX}visual_notes.md"
FRAMESDIR="$OUTDIR/${PREFIX}frames"

BROWSER_HEADERS=()
while IFS= read -r h; do
    BROWSER_HEADERS+=(--add-header "$h")
done < <(python3 "$DIR/config_get.py" download.headers)

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# Phase 1: Try CC subtitles first (fast path, ~3 seconds)
log "检查视频字幕..."
yt-dlp "${BROWSER_HEADERS[@]}" --write-subs --sub-langs all --skip-download -o "$OUTDIR/sub" "$URL" 2>/dev/null || true

SUBS_FILE=$(ls "$OUTDIR"/sub.{json,srt,vtt} 2>/dev/null | head -1 || true)

if [ -n "$SUBS_FILE" ] && [ -s "$SUBS_FILE" ]; then
    log "发现 CC 字幕: $SUBS_FILE"
    python3 "$DIR/transcribe.py" --subs "$SUBS_FILE" --outdir "$OUTDIR" --bv "$BV"
else
    log "无 CC 字幕，下载音频（耗时较长）..."
    rm -f "$OUTDIR"/sub.* 2>/dev/null || true
    yt-dlp "${BROWSER_HEADERS[@]}" -x --audio-format mp3 -o "$OUTDIR/audio.%(ext)s" "$URL"
    log "开始语音转文字（Whisper 推理中）..."
    python3 "$DIR/transcribe.py" --audio "$OUTDIR/audio.mp3" --outdir "$OUTDIR" --bv "$BV"
fi

# Rename transcript files to BV-prefixed
if [ -f "$OUTDIR/transcript.txt" ] && [ "$OUTDIR/transcript.txt" != "$TXTFILE" ]; then
    mv "$OUTDIR/transcript.txt" "$TXTFILE" 2>/dev/null || true
fi
if [ -f "$OUTDIR/transcript.srt" ] && [ "$OUTDIR/transcript.srt" != "$SRTFILE" ]; then
    mv "$OUTDIR/transcript.srt" "$SRTFILE" 2>/dev/null || true
fi

# Phase 2: LLM summarization
log "调用 LLM 总结..."
python3 "$DIR/summarize.py" --transcript "$TXTFILE" --outdir "$OUTDIR" --bv "$BV"

# Rename summary file
if [ -f "$OUTDIR/summary.md" ] && [ "$OUTDIR/summary.md" != "$SUMMARYFILE" ]; then
    mv "$OUTDIR/summary.md" "$SUMMARYFILE" 2>/dev/null || true
fi

# Record to database
python3 "$DIR/db.py" record video "$(python3 -c "import json; print(json.dumps({'bv':'$BV','url':'$URL'}))")" 2>/dev/null || true

# Phase 3: Optional multimodal visual analysis
if [ -n "$ANALYZE" ]; then
    log "开始多模态视觉分析..."
    python3 "$DIR/analyze.py" --video-url "$URL" --outdir "$OUTDIR" --pipeline "$PIPELINE" --bv "$BV"
fi

log "======= 费用明细（上方 [cost] 行） ======="
log "完成！结果在: $OUTDIR"
log "  - ${PREFIX}transcript.txt: 逐字稿"
log "  - ${PREFIX}transcript.srt:  字幕文件"
log "  - ${PREFIX}summary.md:      总结"
if [ -n "$ANALYZE" ]; then
    log "  - ${PREFIX}visual_notes.md: 视觉分析"
    log "  - ${PREFIX}frames/:          截图"
fi
