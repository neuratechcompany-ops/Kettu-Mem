#!/usr/bin/env python3
"""
Backward-compatible launcher — canonical server is at api/server.py (FastAPI).

Usage:
  python3 server.py --data-dir ~/.openclaw/memory-store --port 8765
"""

from api.server import run_server

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Kettu Mem v0.2.0 (shim)")
    parser.add_argument("--data-dir", default="/tmp/mm-server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    run_server(args.data_dir, args.port, args.host)
