"""Bound total parser time and terminate the process group on timeout."""

import json
import os
import signal
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from .features import ROOT


def run_parser(paths: list[Path], *, ocr_mode: str, assessed_on: date,
               freshness_policy: dict, timeout: int = 180) -> dict:
    # Keep nested parser/OCR temporary files inside the private staging tree.
    staging_parent = paths[0].parent if paths else None
    with tempfile.TemporaryDirectory(prefix="hema-parser-", dir=staging_parent) as temporary:
        folder = Path(temporary)
        settings = {"paths": [str(p) for p in paths], "ocr_mode": ocr_mode,
                    "assessed_on": assessed_on.isoformat(), "freshness_policy": freshness_policy,
                    "max_pages": 100, "max_bytes": 20 * 1024 * 1024}
        request_file, result_file = folder / "request.json", folder / "result.json"
        request_file.write_text(json.dumps(settings), "utf-8")
        with (folder / "stderr.log").open("wb") as errors:
            process = subprocess.Popen([sys.executable, "-m", "backend.parse_worker", str(request_file), str(result_file)],
                                       cwd=ROOT, stdout=subprocess.DEVNULL, stderr=errors, start_new_session=True,
                                       env={**os.environ, "TMPDIR": str(folder), "TMP": str(folder), "TEMP": str(folder)})
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    # Windows has no killpg; terminate the worker and its children.
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   check=False)
                    if process.poll() is None:
                        process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise TimeoutError("Распознавание превысило ограничение времени. Попробуйте меньший пакет.")
        if process.returncode or not result_file.exists():
            raise RuntimeError("Не удалось распознать PDF. Проверьте файлы и доступность локального OCR.")
        if result_file.stat().st_size > 32 * 1024 * 1024:
            raise RuntimeError("Результат распознавания слишком большой. Используйте меньший пакет.")
        return json.loads(result_file.read_text("utf-8"))
