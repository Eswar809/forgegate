"""Report queue depth and oldest message age from a snapshot."""

from __future__ import annotations

import json


def run(path: str, queue: str) -> str:
    """Return depth and oldest age for one queue."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    item = data.get("queues", {}).get(queue)
    if item is None:
        return f"unknown queue: {queue}"
    return (
        f"{queue} depth={item.get('depth', 0)} "
        f"oldest_age_min={item.get('oldest_age_min', 0)}"
    )


if __name__ == "__main__":
    import json
    import sys

    if "--call" in sys.argv:
        index = sys.argv.index("--call")
        payload = json.loads(sys.argv[index + 1]) if len(sys.argv) > index + 1 else {}
        print(run(**payload))
    else:
        print("usage: tool.py --call '<json>'", file=sys.stderr)
