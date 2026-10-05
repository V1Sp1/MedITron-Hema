"""Local CLI, JSON output, no file upload or remote inference."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .pipeline import parse_batch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PDF лабораторных анализов → одно наблюдение (JSON)")
    parser.add_argument("files", nargs="+", type=Path, help="PDF одного пакета загрузки")
    parser.add_argument("-o", "--output", type=Path, help="JSON-файл; по умолчанию stdout")
    parser.add_argument("--ocr", choices=["auto", "off", "always"], default="auto",
                        help="Локальный OCR macOS для сканов (по умолчанию auto)")
    parser.add_argument("--as-of", type=date.fromisoformat, help="Дата оценки давности YYYY-MM-DD")
    parser.add_argument("--freshness-policy", type=Path, help="JSON: canonical_analyte -> максимальная давность в днях")
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--max-mb", type=int, default=50)
    args = parser.parse_args(argv)
    try:
        if args.output and args.output.expanduser().resolve() in [p.expanduser().resolve() for p in args.files]:
            raise ValueError("Output cannot overwrite an input PDF")
        policy = json.loads(args.freshness_policy.read_text("utf-8")) if args.freshness_policy else {}
        observation = parse_batch(args.files, assessed_on=args.as_of, freshness_policy=policy,
                                  ocr_mode=args.ocr, max_pages=args.max_pages,
                                  max_bytes=args.max_mb * 1024 * 1024)
        payload = json.dumps(observation.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
            print(f"Наблюдение: {len(observation.documents)} PDF, {len(observation.measurements)} результатов. "
                  f"JSON: {args.output}", file=sys.stderr)
        else:
            print(payload, end="")
        if observation.issues:
            print("Отметки пакета: " + ", ".join(observation.issues), file=sys.stderr)
        # A partial extraction is a usable draft, but an empty one is a failure.
        return 0 if observation.measurements else 2
    except (ValueError, OSError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
