#!/usr/bin/env python3
"""secrets.py -- read a secret from a file, never from a command line, never echoed.

WHY THIS EXISTS. Twice tonight a key reached the transcript because it was interpolated into a
shell command. The rule that prevents it: secrets are READ from a file by a process that needs
them, and they are never printed, logged, or passed as an argument where an argv dump could catch
them.

Usage:
    secrets.py get NAME --file /path/to/env      # prints "present: yes/no", never the value
    secrets.py sha NAME --file /path/to/env      # prints a sha256 prefix for verification only
    secrets.py install NAME --from FILE --to PATH  # writes with 0400, prints byte count only

It parses KEY="value" and KEY=value lines, so token.env and systemd drop-ins both work.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

LINE = re.compile(r'^\s*(?:Environment=)?([A-Z0-9_]+)\s*=\s*(.*)$')


def parse(path: Path) -> dict:
    out = {}
    try:
        for raw in path.read_text().splitlines():
            m = LINE.match(raw)
            if not m:
                continue
            k, v = m.group(1), m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if v:
                out[k] = v
    except OSError:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["get", "sha", "install"])
    ap.add_argument("name")
    ap.add_argument("--file", default="")
    ap.add_argument("--from", dest="src", default="")
    ap.add_argument("--to", dest="dst", default="")
    a = ap.parse_args()

    src = Path(a.file or a.src)
    if not src.exists():
        print("source file not found:", src)
        return 1
    table = parse(src)
    if a.name not in table:
        print("PRESENT: no  (name %s absent from %s)" % (a.name, src))
        return 1

    value = table[a.name]
    if a.command == "get":
        # deliberately never prints the value
        print("PRESENT: yes  name=%s  length=%d" % (a.name, len(value)))
        return 0
    if a.command == "sha":
        print("sha256 prefix:", hashlib.sha256(value.encode()).hexdigest()[:12])
        return 0
    if a.command == "install":
        if not a.dst:
            print("--to required for install")
            return 1
        dst = Path(a.dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        tmp.write_text(value)
        os.chmod(tmp, 0o400)
        os.replace(tmp, dst)
        print("installed: %s  bytes=%d  mode=0400" % (dst, dst.stat().st_size))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
