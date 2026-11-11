#!/usr/bin/env python3
"""Start Studio directly as an API service, without unsloth start."""
import argparse
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--studio", type=Path, required=True, help="Injected Unsloth studio directory")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8888)
args = parser.parse_args()
backend = args.studio.expanduser().resolve() / "backend"
if not (backend / "main.py").is_file() or not (backend / "forged_unsloth/plugin.py").is_file():
    parser.error("--studio must point to an Unsloth studio directory with Forged Unsloth installed")
sys.path.insert(0, str(backend))
import uvicorn
uvicorn.run("main:app", host=args.host, port=args.port)
