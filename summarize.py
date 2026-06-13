import argparse
import json
import os
import sys
from pathlib import Path
from openai import OpenAI


def load_config():
    cfg_path = Path(__file__).parent / "config.json"
    with open(cfg_path) as f:
        return json.load(f)


def resolve_step(config, step_cfg):
    m = step_cfg["model"]
    mdef = config["model_defs"][m]
    return {
        "provider": mdef["provider"],
        "model": mdef["model"],
        "price_input": mdef["price_input"],
        "price_output": mdef["price_output"],
        "temperature": step_cfg.get("temperature"),
        "max_tokens": step_cfg.get("max_tokens"),
    }


def read_api_key(env_dir, key_name):
    key = os.environ.get(key_name)
    if key:
        return key
    env_path = Path(env_dir) / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key_name}="):
                    return line.split("=", 1)[1].strip("\"'")
    return None


def read_transcript(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main():
    parser = argparse.ArgumentParser(description="Summarize transcript with LLM")
    parser.add_argument("--transcript", required=True, help="Path to transcript.txt")
    parser.add_argument("--outdir", required=True, help="Output directory")
    parser.add_argument("--bv", default="", help="BV number for annotation")
    args = parser.parse_args()

    config = load_config()
    sum_cfg = resolve_step(config, config["summarize"])
    provider = config["providers"][sum_cfg["provider"]]
    prompt_text = config["prompts"]["summarize"]

    api_key = read_api_key(Path(__file__).parent, provider["api_key_env"])
    if not api_key:
        print(f"Error: {provider['api_key_env']} not set")
        sys.exit(1)

    transcript = read_transcript(args.transcript)
    print(f"Transcript length: {len(transcript)} chars")

    client = OpenAI(api_key=api_key, base_url=provider["base_url"])
    response = client.chat.completions.create(
        model=sum_cfg["model"],
        messages=[
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": f"请总结以下视频逐字稿：\n\n{transcript}"},
        ],
        temperature=sum_cfg["temperature"],
        max_tokens=sum_cfg["max_tokens"],
    )

    summary = response.choices[0].message.content
    usage = response.usage
    in_t = usage.prompt_tokens
    out_t = usage.completion_tokens
    pi = sum_cfg["price_input"]
    po = sum_cfg["price_output"]
    cost = (in_t * pi + out_t * po) / 1_000_000
    print(f"[cost] 总结（{sum_cfg['model']}）: ¥{cost:.4f}（输入 {in_t} × ¥{pi}/百万 + 输出 {out_t} × ¥{po}/百万）")

    # Prepend BV annotation
    if args.bv:
        summary = f"<!-- BV: {args.bv} -->\n\n{summary}"

    out_path = os.path.join(args.outdir, "summary.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(summary)
    print(f"Summary saved to: {out_path}")

    # Record to database
    try:
        from db import DB
        db = DB()
        bv = args.bv or ""
        if bv:
            db.upsert_video(bv=bv, url="")
        # Get brief (first non-header paragraph)
        brief = ""
        for line in summary.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("<!--") and len(line) > 10:
                brief = line[:500]
                break
        summary_path = out_path  # already full path
        db.add_summary(
            video_id=db.get_video_by_bv(bv)["id"] if bv else 0,
            file_path=summary_path,
            brief=brief,
            input_tokens=in_t,
            output_tokens=out_t,
            cost=cost,
            model=sum_cfg["model"],
        )
        db.close()
    except Exception as e:
        print(f"[db] Warning: {e}")


if __name__ == "__main__":
    main()
