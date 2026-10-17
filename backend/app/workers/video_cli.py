"""Local/operator commands for the execution engine, not a public API."""

import argparse
import json
import sys
from pathlib import Path

from app.workers.tasks.video_tasks import execution_runtime


def main():
    parser = argparse.ArgumentParser(description="Person 3 video execution engine")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("request_file", type=Path)
    enqueue.add_argument("--provider", choices=["fake", "seedance", "poyo", "seedvis"], default="fake")
    for command in ("status", "tick", "cancel"):
        sub.add_parser(command).add_argument("generation_id")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("generation_id")
    reconcile.add_argument("provider_task_id")
    args = parser.parse_args()
    with execution_runtime() as runtime:
        if args.command == "init":
            runtime.store.initialize()
            result = {"initialized": "video_execution_jobs"}
        elif args.command == "enqueue":
            result = runtime.enqueue(
                args.provider, json.loads(args.request_file.read_text("utf-8"))
            )
        elif args.command == "tick":
            result = runtime.tick(args.generation_id)
        elif args.command == "cancel":
            result = runtime.store.request_cancel(args.generation_id)
        elif args.command == "reconcile":
            result = runtime.reconcile(args.generation_id, args.provider_task_id)
        else:
            result = runtime.store.get(args.generation_id)
        # Avoid echoing prompts, signed input URLs and full provider output in operator logs.
        result = {
            key: value
            for key, value in result.items()
            if key not in {"payload", "fingerprint", "result", "lease_token"}
        }
        sys.stdout.write(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
