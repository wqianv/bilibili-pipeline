#!/usr/bin/env python3
"""Read config.json values for bash. Usage:
  python3 config_get.py download.headers        # print each header on its own line
  python3 config_get.py pipelines.agnes_full.fallback  # print single value
"""
import json, sys
from pathlib import Path

cfg = json.loads((Path(__file__).parent / "config.json").read_text())

key = sys.argv[1]
parts = key.split(".")
val = cfg
for p in parts:
    val = val[p]

if isinstance(val, list):
    for item in val:
        print(item)
elif isinstance(val, dict):
    print(json.dumps(val, ensure_ascii=False))
else:
    print(val)
