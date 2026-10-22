"""Inspect sync-job snapshots and report the latest failure for a customer."""

from __future__ import annotations

import json


def run(path: str, customer: str) -> str:
    """Return the latest failed sync job for the given customer."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    failed = [
        job for job in data.get("jobs", [])
        if job.get("customer") == customer and job.get("status") == "failed"
    ]
    if not failed:
        return f"No failed jobs for {customer}"
    job = sorted(failed, key=lambda item: item.get("finished_at", ""))[-1]
    return (
        f"{job['id']} | {job.get('error', 'unknown')} | "
        f"{job.get('finished_at', '')}"
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
