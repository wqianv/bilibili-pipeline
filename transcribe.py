import argparse
import json
import os
import sys

def convert_subs_to_text(subs_path, outdir):
    """Convert Bilibili subtitle JSON or SRT to plain text."""
    text_path = os.path.join(outdir, "transcript.txt")
    srt_path = os.path.join(outdir, "transcript.srt")

    ext = os.path.splitext(subs_path)[1].lower()

    if ext == ".json":
        with open(subs_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        segments = []
        for item in data.get("body", []):
            start = item.get("from", 0)
            end = item.get("to", 0)
            content = item.get("content", "")
            segments.append((start, end, content))
    elif ext == ".srt":
        segments = parse_srt(subs_path)
    else:
        print(f"Unsupported subtitle format: {ext}")
        sys.exit(1)

    write_srt(segments, srt_path)
    write_text(segments, text_path)
    print(f"Subtitles saved to: {text_path}")
    return text_path


def parse_srt(path):
    """Parse an SRT file into (start, end, text) tuples."""
    segments = []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().strip().split("\n\n")
    for block in lines:
        parts = block.split("\n")
        if len(parts) < 3:
            continue
        time_line = parts[1]
        text = " ".join(parts[2:])
        start, end = parse_srt_time(time_line)
        segments.append((start, end, text))
    return segments


def parse_srt_time(time_line):
    """Parse SRT timestamp '00:01:23,456 --> 00:01:25,789'."""
    parts = time_line.split(" --> ")
    start = ts_to_sec(parts[0])
    end = ts_to_sec(parts[1])
    return start, end


def ts_to_sec(ts):
    h, m, s = ts.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def sec_to_ts(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def write_srt(segments, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, (start, end, text) in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{sec_to_ts(start)} --> {sec_to_ts(end)}\n")
            f.write(f"{text}\n\n")


def write_text(segments, path):
    with open(path, "w", encoding="utf-8") as f:
        for _, _, text in segments:
            f.write(text.strip() + "\n")


def transcribe_audio(audio_path, outdir, model_size="medium"):
    """Transcribe audio with faster-whisper."""
    from faster_whisper import WhisperModel

    text_path = os.path.join(outdir, "transcript.txt")
    srt_path = os.path.join(outdir, "transcript.srt")

    print(f"Loading Whisper model ({model_size})...")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    print(f"Transcribing {audio_path}...")
    segments, info = model.transcribe(audio_path, language="zh", beam_size=5)

    seg_list = []
    for seg in segments:
        seg_list.append((seg.start, seg.end, seg.text.strip()))

    write_srt(seg_list, srt_path)
    write_text(seg_list, text_path)
    print(f"Transcription saved to: {text_path}")
    return text_path


def main():
    parser = argparse.ArgumentParser(description="Bilibili video transcription")
    parser.add_argument("--audio", help="Path to audio file")
    parser.add_argument("--subs", help="Path to subtitle file (JSON/SRT)")
    parser.add_argument("--outdir", required=True, help="Output directory")
    parser.add_argument("--model", default="base", help="Whisper model size")
    parser.add_argument("--bv", default="", help="BV number for annotation")
    args = parser.parse_args()

    if args.subs:
        convert_subs_to_text(args.subs, args.outdir)
    elif args.audio:
        transcribe_audio(args.audio, args.outdir, args.model)
    else:
        print("Either --audio or --subs is required")
        sys.exit(1)

    # Prepend BV annotation to transcript.txt
    if args.bv:
        text_path = os.path.join(args.outdir, "transcript.txt")
        if os.path.exists(text_path):
            with open(text_path, "r", encoding="utf-8") as f:
                content = f.read()
            header = f"[BV: {args.bv}]\n"
            if not content.startswith(header):
                with open(text_path, "w", encoding="utf-8") as f:
                    f.write(header + content)


if __name__ == "__main__":
    main()
