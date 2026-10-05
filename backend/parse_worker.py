"""Isolated local parser entry point, invoked with local JSON filenames."""

import json
import sys
from datetime import date
from pathlib import Path

from .worker_limits import apply_limits


def main():
    job_handle = apply_limits()
    from lab_parser import parse_batch
    settings = json.loads(Path(sys.argv[1]).read_text("utf-8"))
    observation = parse_batch(settings["paths"], ocr_mode=settings["ocr_mode"],
                              assessed_on=date.fromisoformat(settings["assessed_on"]),
                              freshness_policy=settings["freshness_policy"],
                              max_pages=settings["max_pages"], max_bytes=settings["max_bytes"])
    Path(sys.argv[2]).write_text(json.dumps(observation.to_dict(), ensure_ascii=False, allow_nan=False), "utf-8")


if __name__ == "__main__":
    main()
