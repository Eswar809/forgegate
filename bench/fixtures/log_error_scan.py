"""Scan an application log and return matching error lines."""

from __future__ import annotations

import re


def run(path: str, level: str = "ERROR", service: str = "") -> str:
    """Return a count header followed by matching log lines."""
    pattern = re.compile(rf"\b{re.escape(level)}\b")
    matches = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not pattern.search(line):
                continue
            if service and service not in line:
                continue
            matches.append(line.rstrip())
    return "\n".join([f"count={len(matches)}"] + matches)


if __name__ == "__main__":
    import json
    import sys

    if "--call" in sys.argv:
        index = sys.argv.index("--call")
        payload = json.loads(sys.argv[index + 1]) if len(sys.argv) > index + 1 else {}
        print(run(**payload))
    else:
        print("usage: tool.py --call '<json>'", file=sys.stderr)
