"""Compare two JSON schema snapshots and report column changes."""

from __future__ import annotations

import json


def run(before_path: str, after_path: str) -> str:
    """Return added and removed columns per table."""
    with open(before_path, encoding="utf-8") as handle:
        before = json.load(handle)
    with open(after_path, encoding="utf-8") as handle:
        after = json.load(handle)

    lines = []
    for table in sorted(set(before) | set(after)):
        old = set(before.get(table, []))
        new = set(after.get(table, []))
        added = sorted(new - old)
        removed = sorted(old - new)
        if not added and not removed:
            continue
        parts = []
        if added:
            parts.append("+ " + ", ".join(added))
        if removed:
            parts.append("- " + ", ".join(removed))
        lines.append(f"{table}: " + "; ".join(parts))
    return "\n".join(lines) if lines else "no schema changes"


if __name__ == "__main__":
    import json
    import sys

    if "--call" in sys.argv:
        index = sys.argv.index("--call")
        payload = json.loads(sys.argv[index + 1]) if len(sys.argv) > index + 1 else {}
        print(run(**payload))
    else:
        print("usage: tool.py --call '<json>'", file=sys.stderr)
