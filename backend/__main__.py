import argparse
import json
from pathlib import Path

import uvicorn

from .app import create_app
from .model_service import DEFAULT_MODEL_BUNDLE


def main():
    parser = argparse.ArgumentParser(description="Hema: локальный сайт и API")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--ocr", choices=["auto", "off", "always"], default="auto")
    parser.add_argument("--freshness-policy", type=Path)
    parser.add_argument("--phrase-file", type=Path)
    model_options = parser.add_mutually_exclusive_group()
    model_options.add_argument("--model-bundle", type=Path, default=DEFAULT_MODEL_BUNDLE,
                               help="Доверенный локальный joblib с выбранными обученными моделями")
    model_options.add_argument("--no-model", action="store_true", help="Явно отключить модель, оставить правило Hb")
    parser.add_argument("--trusted-model-sha256", help="SHA-256 самостоятельно проверенного нового файла весов; подтверждает доверие к исполняемому содержимому")
    parser.add_argument("--no-ferritin-model", action="store_true", help="Отключить отдельный прогноз низкого ферритина v4")
    args = parser.parse_args()
    policy = json.loads(args.freshness_policy.read_text("utf-8")) if args.freshness_policy else None
    from .ferritin_service import DEFAULT_FERRITIN_BUNDLE
    app = create_app(data_dir=args.data_dir, ocr_mode=args.ocr,
                     freshness_policy=policy, phrase_file=args.phrase_file,
                     model_bundle=None if args.no_model else args.model_bundle, trusted_model_sha256=args.trusted_model_sha256,
                     ferritin_bundle=None if args.no_ferritin_model else DEFAULT_FERRITIN_BUNDLE)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
