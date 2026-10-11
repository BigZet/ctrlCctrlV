"""Measure owned engine RSS after warming installed models; no user capture or clipboard."""
import argparse
import json
from pathlib import Path
import threading
import time

from ctrlcctrlv.catalog import MODELS
from ctrlcctrlv.diagnostics import runtime_report, system_memory
from ctrlcctrlv.runtime import LocalRuntime
from ctrlcctrlv.settings import data_directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=data_directory())
    parser.add_argument("--output", type=Path, default=Path("artifacts/memory-benchmark.json"))
    args = parser.parse_args()
    runtime = LocalRuntime(args.data_dir, session="review")
    rows = []
    physical = system_memory()
    try:
        for identifier in MODELS:
            if not runtime.installed(identifier):
                continue
            started = time.monotonic()
            runtime.start(identifier, threading.Event(), lambda label, *_: print(label, flush=True))
            row = {"model": identifier, "preparation_elapsed": time.monotonic() - started,
                   "physical_memory_bytes": physical, "after_warmup": runtime_report(runtime)}
            rows.append(row)
            print(json.dumps(row), flush=True)
            runtime.stop()
    finally:
        runtime.stop()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
