"""JSONL session logging for the uvradio MCP server.

Every tool call and its result is appended to logs/session-<ts>-<id>.jsonl, one
self-contained JSON object per line — append-only, tail-able, and trivially
streamed to the live viewer (tools/mcp/logviewer.py).

Format
------
Line 1 is a session header; then a paired `call` / `result` (or `error`) per tool
invocation, linked by `id`, so a live view can show a call in flight and time it:

  {"ts":"…","type":"session","session":"a1b2c3d4","git":{...},"firmware":{...}}
  {"ts":"…","type":"call","id":1,"tool":"press_keys","args":{"keys":"MENU"}}
  {"ts":"…","type":"result","id":1,"tool":"press_keys","ok":true,"dur_ms":812,
   "text":"pressed […]","images":[{"path":"artifacts/0001-press_keys.png",…}]}

Images are stored by REFERENCE (written to artifacts/, referenced by path+hash),
never base64-inlined — screenshots would bloat the log beyond usefulness.

NOTE: the MCP server speaks protocol on stdout, so nothing here may ever print
there. Everything goes to the log file (and stderr on failure).
"""
import hashlib
import itertools
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _git(fw):
    try:
        def g(*a):
            return subprocess.run(["git", "-C", str(fw), *a], capture_output=True,
                                  text=True, timeout=5).stdout.strip()
        return {"branch": g("rev-parse", "--abbrev-ref", "HEAD"),
                "sha": g("rev-parse", "--short", "HEAD"),
                "dirty": bool(g("status", "--porcelain"))}
    except Exception:
        return {}


class SessionLog:
    def __init__(self, log_dir, fw, elf=None):
        self.dir = Path(log_dir)
        self.art = self.dir / "artifacts"
        self.art.mkdir(parents=True, exist_ok=True)
        self.session = os.urandom(4).hex()
        self.path = self.dir / f"session-{datetime.now():%Y%m%d-%H%M%S}-{self.session}.jsonl"
        self._seq = itertools.count(1)
        self._lock = threading.Lock()
        self._t0 = time.monotonic()   # events carry t_ms since session start

        fwinfo = {}
        try:
            if elf and Path(elf).exists():
                st = Path(elf).stat()
                fwinfo = {"elf": str(elf), "bytes": st.st_size,
                          "built": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}
        except Exception:
            pass
        self.event({"type": "session", "session": self.session, "pid": os.getpid(),
                    "cwd": str(fw), "git": _git(fw), "firmware": fwinfo})

    def next_id(self):
        return next(self._seq)

    def event(self, obj):
        obj = {"ts": _now(), "t_ms": round((time.monotonic() - self._t0) * 1000), **obj}
        line = json.dumps(obj, default=str, ensure_ascii=False)
        try:
            with self._lock, open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        except Exception as e:                       # never break a tool over logging
            print(f"[session_log] write failed: {e}", file=sys.stderr)

    def save_png(self, data: bytes, cid: int, tag: str):
        name = f"{cid:04d}-{tag}.png"
        try:
            (self.art / name).write_bytes(data)
        except Exception as e:
            print(f"[session_log] artifact write failed: {e}", file=sys.stderr)
            return None
        return {"path": f"artifacts/{name}",
                "sha256": hashlib.sha256(data).hexdigest()[:16],
                "bytes": len(data)}

    def harvest(self, result, cid, tool):
        """Pull text + images out of an MCP tool result, saving PNGs to artifacts."""
        import base64
        texts, images = [], []

        def walk(x):
            if x is None:
                return
            if isinstance(x, (list, tuple)):
                for i in x:
                    walk(i)
                return
            tn = type(x).__name__
            if tn == "TextContent":
                texts.append(x.text)
            elif tn == "ImageContent":
                try:
                    meta = self.save_png(base64.b64decode(x.data), cid, tool)
                    if meta:
                        images.append(meta)
                except Exception:
                    pass
            elif isinstance(x, dict):
                # structured result dict — keep it small
                texts.append(json.dumps(x, default=str)[:2000])
            elif isinstance(x, str):
                texts.append(x)

        walk(result)
        return texts, images
