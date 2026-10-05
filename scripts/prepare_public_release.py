"""Prepare a separate, reviewed public source snapshot for the MedITron jury.

Original data, trained artifacts and the working checkout remain untouched.
Only explicit handoff files are copied. Model metadata is minimized; fitted
estimators, feature order, thresholds and calibration parameters are preserved.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXCLUDED_ROOT = {"AGENTS.md", "PROJECT_CONTEXT.md", "DEMO_ACCESS_OPTIONS.md", "README.md", "START_HERE.html"}
EXCLUDED_DATA = {
    "data/case/case_current.pdf", "data/case/case_original_superseded.pdf",
    "data/reference/medical_text_corrections_2026-10-04.docx",
}
EXCLUDED_EVIDENCE = {
    "docs/security_audit_evidence.json", "docs/security_remediation_evidence.json",
    "docs/handoff_verification.json", "docs/research_modal_verification.json",
}
TEXT_EXTENSIONS = {".py", ".swift", ".js", ".mjs", ".css", ".html", ".md", ".json", ".toml", ".txt", ".svg", ".csv"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prediction_difference(a, b):
    if isinstance(a, dict):
        if set(a) != set(b):
            raise AssertionError("Prediction fields differ")
        return max((prediction_difference(a[k], b[k]) for k in a), default=0.)
    if isinstance(a, list):
        if len(a) != len(b):
            raise AssertionError("Prediction lengths differ")
        return max((prediction_difference(x, y) for x, y in zip(a, b)), default=0.)
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b))
    if a != b:
        raise AssertionError("Prediction policy/metadata differs")
    return 0.


def portable_text(text: str) -> str:
    text = text.replace(str(ROOT / "data/case/deficiency_anemia.csv"), "data/case/deficiency_anemia.csv")
    text = re.sub(r"/Users/[^/\s]+/Downloads/\(СУ\)deficiency_anemia\.csv", "data/case/deficiency_anemia.csv", text)
    text = text.replace(str(ROOT), ".")
    text = re.sub(r"/Users/[^/\s]+/Downloads/[^\n`\"<>|]+", "исходный материал кейса (не включён)", text)
    text = re.sub(r"/(?:private/)?var/folders/[^\s`\"<>|]+", "temporary-verification-directory", text)
    text = text.replace("PROJECT_CONTEXT.md", "RELEASE_NOTES.md")
    return text


def portable_json(value):
    if isinstance(value, str):
        return portable_text(value)
    if isinstance(value, dict):
        return {portable_text(k): portable_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [portable_json(v) for v in value]
    return value


def clean_model_strings(value, seen=None):
    """Minimize path strings in estimator metadata without touching arrays."""
    if seen is None:
        seen = set()
    if isinstance(value, str):
        return portable_text(value)
    if isinstance(value, Path):
        return Path(portable_text(str(value)))
    if value is None or isinstance(value, (int, float, bool, bytes)):
        return value
    if id(value) in seen:
        return value
    seen.add(id(value))
    if isinstance(value, dict):
        for key in list(value):
            value[key] = clean_model_strings(value[key], seen)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            value[i] = clean_model_strings(item, seen)
    elif isinstance(value, tuple):
        return tuple(clean_model_strings(item, seen) for item in value)
    elif hasattr(value, "__dict__") and type(value).__module__.startswith(("sklearn.", "catboost.", "ml_baselines.")):
        for key, item in vars(value).items():
            setattr(value, key, clean_model_strings(item, seen))
    return value


README = """# Hema — MedITron 2026

Исследовательский прототип команды **NeuroNiXxx** по кейсу Сеченовского университета. Принимает лабораторные показатели вручную, из CSV/XLSX или текстового PDF, показывает черновые пояснения для пациента и врача и формирует PDF/JSON-отчёт.

**Выпуск для жюри MedITron. Только синтетические или действительно обезличенные данные. Проект не предназначен для постановки диагноза, назначения лечения или обработки идентифицируемых данных реальных пациентов. Клиническая валидация не выполнена.**

## Посмотреть результат

![Пациентский сценарий](docs/media/hema_patient_demo.gif)

Демонстрация сделана на вымышленных анализах с ответами настоящего локального API. [Врачебный сценарий](docs/media/hema_doctor_demo.gif). [Краткий маршрут проверки для жюри](docs/JURY_GUIDE.md).

## Скачать и запустить

1. На странице репозитория нажмите **Code → Download ZIP** и распакуйте архив целиком. Можно также клонировать репозиторий: `git clone https://github.com/V1Sp1/MedITron-Hema.git`.
2. Откройте **START_HERE.html** в распакованной папке. [Подробная инструкция macOS и Windows](docs/06_LOCAL_SETUP.md) объясняет установку Python и первый запуск без предварительных технических навыков.
3. Установите **Python 3.12, 64-bit**. Для macOS выполните из папки проекта `bash START_MAC.command`; на Windows дважды щёлкните **START_WINDOWS.bat**.
4. После установки библиотек откройте **http://127.0.0.1:8000/**. Окно сервера оставьте открытым. Для первого запуска нужен интернет; вычисления выполняются локально.

Готовые веса включены. Повторное обучение, Node.js, платные медицинские API и аккаунт инструмента разработки для основного запуска не нужны. На Windows работают текстовые PDF; OCR сканов в этом выпуске доступен только на macOS.

Ручной запуск на macOS:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -c requirements-runtime.txt -e '.[server,model]'
.venv/bin/python -m backend --ocr off
```

Ручной запуск в Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -c requirements-runtime.txt -e ".[server,model]"
.\\.venv\\Scripts\\python.exe -m backend --ocr off
```

## Что реализовано

- Пациентский и врачебный интерфейсы, вход врача, ввод 37 признаков и проверка единиц.
- Импорт CSV/XLSX/PDF, проверка распознанных значений человеком, понятные сообщения при неполной панели.
- Правило Hb из кейса, исследовательские оценки baseline_v1 и отдельный прогноз низкого ферритина biochemical_v4 при поддержанном вводе. Измеренный ферритин показывается напрямую; для прогноза требуется определённый контекст беременности.
- Версионированные черновые пояснения, экспорт PDF с кириллицей и JSON.
- Изоляция исследовательских сеансов и врачей, временные медицинские объекты в RAM до часа, удаление, stateless API для тестовых организаций с отзываемыми ключами.

## Архитектура и подробные пояснения

| Раздел | Руководство |
|---|---|
| Каталоги, файлы и архитектура | [Структура проекта](docs/01_PROJECT_STRUCTURE.md) |
| Данные, обучение, выбор и ограничения | [Модели](docs/02_MODEL.md) |
| API, PDF, хранение и доступ | [Бэкенд](docs/03_BACKEND.md) |
| Страницы, импорт и отчёт | [Фронтенд](docs/04_FRONTEND.md) |
| Пояснения и правила их выбора | [Рекомендации](docs/05_RECOMMENDATIONS.md) |
| Установка и устранение проблем | [macOS и Windows](docs/06_LOCAL_SETUP.md) |
| Методика проверок и их границы | [Проверки](docs/07_VERIFICATION.md) |
| Исправления безопасности | [Исследовательский контур](docs/09_SECURITY_REMEDIATION.md) |
| Работающая связка v1 + ферритин v4 | [Гибридный алгоритм](docs/10_HYBRID_MODEL.md) |

Один FastAPI-сервер отдаёт статический фронтенд и API. PDF разбирает отдельный Python-worker. Файлы моделей проверяются по SHA-256 перед загрузкой. Учётные записи сохраняются локально отдельно от временных медицинских объектов.

## Как оценивались модели

baseline_v1 обучен на 840 частично синтетических строках кейса; внутренние метрики не доказывают качество на реальных пациентах. Отдельный ферритин v4 проверен на новых циклах NHANES. Для расширенной панели внешний AUROC низкого ферритина — **0,870867**, F1 — **0,616**; это результат конкретной биохимической задачи и выборки, а не точность диагноза.

[Отчёт baseline_v1](experiments/baseline_v1/REPORT.md), [внешняя проверка v1/v2/v3](experiments/external_validation_v1/REPORT.md), [biochemical_v4](experiments/biochemical_v4/REPORT.md). B12 остаётся слабым; B12/PLP v4 доступны только в отдельном исследовательском CLI. Веса, пороги и выводы не переобучались для публикации.

## Разработка и проверки

Для тестов дополнительно установите `.[test]` в созданное окружение:

```sh
.venv/bin/python -m pip install -c requirements-runtime.txt -e '.[server,model,test]'
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/verify_handoff.py --output public_smoke_verification.json
```

На Windows используйте `.\\.venv\\Scripts\\python.exe` вместо `.venv/bin/python`. Для фронтенд-тестов нужен Node.js 20+: из папки `frontend` выполните `npm test` и `npm run build`. Исследовательские проверки с исключёнными построчными наборами пропускаются с явной причиной. Проверки запуска не являются клинической сертификацией.

## Состав публичного выпуска

Включены исходники, подробная документация Markdown/HTML, выбранные модели четырёх семейств, агрегированные результаты и синтетические примеры. Исходные медицинские таблицы, списки обучающих ID, индивидуальные прогнозы, базы аккаунтов, загруженные документы, секреты, личные пути и история рабочего окружения исключены.

В публичных копиях весов удалены только служебные списки ID и пути, обновлены метаданные словаря и хеши доверия. Численные параметры и прогнозы сохранены; исходные артефакты исследования не изменены. Подробности — [RELEASE_NOTES.md](RELEASE_NOTES.md), контрольные суммы — FILES_MANIFEST.json и MODEL_PUBLICATION.json.

Публичная публикация не означает юридического или медицинского допуска к использованию на пациентах. [Документы подготовки будущего клинического выпуска](docs/legal/README.md) являются незаполненными шаблонами. Условия сторонних компонентов и происхождение данных — [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
"""

JURY_GUIDE = """# Проверка Hema для жюри MedITron

## Быстрый маршрут

1. Скачайте весь репозиторий (Code → Download ZIP), распакуйте и выполните [инструкцию своей ОС](06_LOCAL_SETUP.md). Готовые веса уже включены.
2. Откройте http://127.0.0.1:8000/, выберите «Я пациент», подтвердите исследовательский режим и **синтетические данные**.
3. Загрузите `examples/synthetic_panel.csv`, проверьте поля и нажмите расчёт. Это вызывает настоящую модель; «Посмотреть пример» показывает заранее подготовленный отчёт.
4. Откройте пояснения и скачайте PDF. Сверьте значения, кириллицу и отдельный блок ферритина. В этом CSV ферритин измерен, поэтому v4-прогноз для него не нужен.
5. Для PDF-импорта используйте `output/pdf/test_uploads/01_cbc.pdf` и `02_iron.pdf`. Оба файла — вымышленные учебные бланки. Можно загрузить пакет одновременно. Проверьте извлечённые значения перед расчётом.
6. Для прогноза отсутствующего ферритина значения в `examples/biochemical_v4_input.json` задают подходящую синтетическую панель; перенесите их в форму, ферритин оставьте пустым и выберите «Беременность исключена» для этой вымышленной ситуации. При неизвестном контексте программа воздерживается.
7. Нажмите «Очистить». Объекты текущего сеанса удалятся. Скачанный PDF останется у вас.

## Врачебный интерфейс

В другой вкладке откройте роль врача. Создайте отдельную **вымышленную** учётную запись по [backend/AUTH.md](../backend/AUTH.md); пароль вводится локально и не опубликован в репозитории. Вход, исследовательское подтверждение, CSV/PDF-импорт, расчёт и PDF работают в том же сервере. Записи другого врача или исследовательского сеанса недоступны.

## Что означает результат

Порог Hb — правило кейса. Scores baseline_v1 — исследовательские оценки модели, а не подтверждённые вероятности болезни. Низкий ферритин v4 — отдельная биохимическая задача с ограниченной областью применения. При недостаточном вводе или неподдержанной популяции вывод может быть неизвестным; это ожидаемое поведение. Тексты следующих шагов являются черновыми и не назначают лечение.

Для оценки архитектуры откройте [структуру](01_PROJECT_STRUCTURE.md), [модели](02_MODEL.md), [бэкенд](03_BACKEND.md), [фронтенд](04_FRONTEND.md), [рекомендации](05_RECOMMENDATIONS.md) и [гибридный алгоритм](10_HYBRID_MODEL.md). Графики и агрегированные метрики находятся в `experiments/`. Построчные исходники не включены; повторное обучение требует отдельно правомерно полученного набора.

## Видеодемонстрации и ограничения запуска

[Пациентский сценарий](media/hema_patient_demo.gif) и [врачебный сценарий](media/hema_doctor_demo.gif) сняты на вымышленных анализах. Проверка выпуска выполняется на macOS Apple Silicon; нативный запуск Windows и чистая первая установка на другом компьютере пока не подтверждены. Windows поддерживает текстовые PDF, а OCR сканов доступен только на macOS. Не используйте настоящие идентифицируемые документы пациентов.
"""

NOTICES = """# Сторонние компоненты и происхождение материалов

Публичный исследовательский выпуск Hema подготовлен для оценки жюри MedITron. Отдельная открытая лицензия на оригинальный код и веса в этом выпуске не назначена. Для использования за пределами доступных по условиям GitHub прав просмотра и fork необходимо согласовать права с правообладателями; этот документ не заменяет лицензии сторонних компонентов.

## Включённые компоненты

| Компонент | Лицензия в репозитории | Источник |
|---|---|---|
| JSZip | frontend/src/vendor/JSZIP-LICENSE.md | https://github.com/Stuk/jszip |
| pdf-lib | frontend/src/vendor/pdf-lib-LICENSE.md | https://github.com/Hopding/pdf-lib |
| fontkit для pdf-lib | frontend/src/vendor/fontkit-LICENSE.txt | https://github.com/Hopding/fontkit |
| Шрифты IBM Plex | frontend/src/assets/fonts/LICENSE.txt | https://github.com/IBM/plex |

Python-зависимости устанавливаются отдельно из PyPI по pyproject.toml и requirements-runtime.txt; их лицензии сохраняются в устанавливаемых пакетах. Бинарные окружения и установщики зависимостей не публикуются.

## Исследовательские источники

baseline_v1 и кандидаты v2/v3 связаны с кейсом Сеченовского университета: 840 частично синтетических строк, 48 столбцов. Исходный CSV и построчные производные не включены. Условия публичной передачи проектных материалов подтверждены владельцем публикации; это не самостоятельная лицензия на исходный набор или его последующее использование.

NHANES — источник открытых лабораторных измерений и документации CDC/NCHS для внешней биохимической проверки и v4. Первоисточник: https://wwwn.cdc.gov/nchs/nhanes/ . Включены методика, агрегированные результаты и исследовательские модели; индивидуальные строки не распространяются в этом снимке.

Отдельный опыт частичного дообучения использовал набор Serhat Kılıçarslan, Mete Celik, Safak Şahin, Anemia Disease, Mendeley Data, DOI https://doi.org/10.17632/dt89jydgnv.1 , CC BY 4.0. Этот опыт не заменил рабочие модели; его построчные наборы и отдельные дообученные веса не включены. Полные сведения о проверке источников и изменениях — EXTERNAL_DATASETS.md и KILICARSLAN_ANALYSIS.md.

Синтетические панели, учебные PDF и демонстрации созданы для проекта; они не содержат исходных записей пациентов. Иллюстрации интерфейса созданы для проекта, промпты лежат рядом с изображениями. Медицинские пояснения остаются черновыми, их источники и редакционные ограничения описаны в документации рекомендаций.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "deliverables/github/MedITron-Hema")
    args = parser.parse_args()
    target = args.output.resolve()
    if target == ROOT or not target.is_relative_to(ROOT / "deliverables"):
        parser.error("output must be a separate directory inside deliverables")
    if target.exists() and any(target.iterdir()):
        parser.error("output must be empty; existing publication snapshots are not overwritten")
    target.mkdir(parents=True, exist_ok=True)

    spec = importlib.util.spec_from_file_location("hema_handoff_builder", ROOT / "scripts/bundle_project.py")
    handoff = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handoff)
    copied = []
    for source in handoff.files():
        rel = source.relative_to(ROOT)
        name = rel.as_posix()
        if name in EXCLUDED_DATA | EXCLUDED_EVIDENCE or len(rel.parts) == 1 and name in EXCLUDED_ROOT:
            continue
        if rel.suffix == ".html" and source.with_suffix(".md").exists():
            continue
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        if rel.suffix == ".json":
            original = json.loads(dest.read_text("utf-8"))
            cleaned = portable_json(original)
            if cleaned != original:
                dest.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        elif rel.suffix in TEXT_EXTENSIONS:
            original = dest.read_text("utf-8")
            cleaned = portable_text(original)
            if cleaned != original:
                dest.write_text(cleaned, encoding="utf-8")
        copied.append(name)

    # Keep full fitted estimators but remove row identifiers and personal paths.
    import joblib
    import numpy as np
    from backend.model_service import ModelService
    from ml_baselines.biochemical import predict_biochemical
    from ml_baselines.biochemical_predict import load_candidate
    from ml_baselines.predict import predict

    synthetic = json.loads((ROOT / "examples/biochemical_v4_input.json").read_text("utf-8"))
    rng = np.random.default_rng(20261005)
    views = [synthetic, {"age_years": 42, "sex": "F", "hemoglobin": 108},
             {"age_years": 60, "sex": "M", "hemoglobin": 145}]
    for _ in range(24):
        view = {k: v for k, v in synthetic.items() if k in {"age_years", "sex"} or rng.random() >= .3}
        views.append(view)
    models = []
    for family in ("baseline_v1", "robust_v2", "calibrated_v3", "biochemical_v4"):
        relative = Path(f"experiments/{family}/models/selected.joblib")
        source = ROOT / relative
        if family == "biochemical_v4":
            load_candidate(source)  # Verify trust before any deserialization.
            bundle = joblib.load(source)
            infer = lambda b, x: predict_biochemical(b, x, "not_pregnant")
            before = [infer(bundle, v) for v in views]
            groups = bundle.pop("training_patients", {})
            bundle["training_patient_counts"] = {str(k): len(v) for k, v in groups.items()}
            bundle["dictionary_file_sha256"] = digest(target / "data/feature_dictionary.json")
            removed = ["training_patients"]
        else:
            service = ModelService(source)
            if service.bundle is None:
                raise ValueError("Source model is not trusted: " + family)
            bundle = service.bundle
            infer = lambda b, x: {route: predict(b, x, route) for route in ("primary", "cbc")}
            before = [infer(bundle, v) for v in views]
            bundle["training_row_count"] = len(bundle.pop("training_rows", []))
            bundle["experiment"] = f"experiments/{family}"
            removed = ["training_rows", "absolute experiment path"]
        clean_model_strings(bundle)
        destination = target / relative
        joblib.dump(bundle, destination, compress=3)
        reloaded = joblib.load(destination)
        after = [infer(reloaded, v) for v in views]
        difference = prediction_difference(before, after)
        if difference > 1e-12:
            raise AssertionError("Metadata minimization changed predictions: " + family)
        models.append({"family": family, "path": relative.as_posix(), "original_sha256": digest(source),
                       "public_sha256": digest(destination), "removed_metadata": removed,
                       "synthetic_panels_checked": len(views), "predictions_equivalent_at_1e_12": True,
                       "max_absolute_difference": difference})

    # Saved demonstrations remain real-inference snapshots with public artifact IDs.
    for path in target.rglob("*"):
        if not path.is_file() or path.suffix not in {".json", ".js", ".md", ".html"}:
            continue
        relative = path.relative_to(target)
        if relative.parts[0] not in {"frontend", "examples", "docs"}:
            continue
        text = path.read_text("utf-8")
        updated = text
        for model in models[:3]:
            prefix = "hema-" + model["family"].replace("_", "-") + "-"
            updated = updated.replace(prefix + model["original_sha256"][:12], prefix + model["public_sha256"][:12])
        if updated != text:
            path.write_text(updated, encoding="utf-8")

    trust_path = target / "backend/data/model_trust.json"
    trust = json.loads(trust_path.read_text("utf-8"))
    for model in models[:3]:
        trust["sha256"][model["path"]] = model["public_sha256"]
    trust_path.write_text(json.dumps(trust, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    trust_path = target / "backend/data/biochemical_model_trust.json"
    trust = json.loads(trust_path.read_text("utf-8"))
    entry = trust["bundles"][models[3]["path"]]
    entry["sha256"] = models[3]["public_sha256"]
    entry["external_gates_sha256"] = digest(target / "experiments/biochemical_v4/external_gates.json")
    trust_path.write_text(json.dumps(trust, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Historical experiment locks describe original research artifacts; do not relabel them.
    (target / "MODEL_PUBLICATION.json").write_text(json.dumps({"date": "2026-10-05", "models": models,
        "note": "Only publication metadata was minimized; original experiment locks refer to original_sha256. No retraining, threshold change or test-based selection."}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    (target / "README.md").write_text(README, encoding="utf-8")
    (target / "THIRD_PARTY_NOTICES.md").write_text(NOTICES, encoding="utf-8")
    (target / "docs/JURY_GUIDE.md").write_text(JURY_GUIDE, encoding="utf-8")
    for name in ("hema_patient_demo.gif", "hema_doctor_demo.gif"):
        destination = target / "docs/media" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "deliverables/presentation" / name, destination)
    (target / ".gitignore").write_text(""".venv/
__pycache__/
*.py[cod]
*.egg-info/
node_modules/
frontend/dist/
.pytest_cache/
.cache/
.ast-index/
.DS_Store
.env
.env.*
*.sqlite*
*.db
*.key
*.pem
*.log
**/.hema-runtime
data/local*/
data/external/raw/
data/external/review_only/
data/external/processed/**/*.csv
data/case/*.csv
experiments/**/splits.csv
experiments/**/split_audit.json
experiments/**/oof*.csv
experiments/**/holdout_predictions.csv
experiments/**/external_predictions.csv
experiments/**/nhanes_*_features.csv
experiments/**/nhanes_*_metadata.csv
tmp/
deliverables/
public_smoke_verification.json
""", encoding="utf-8")
    (target / "RELEASE_NOTES.md").write_text("""# Публичный выпуск Hema для MedITron — 05.10.2026

Текущая рабочая сборка: baseline_v1 + отдельный низкий ферритин v4. Включены выбранные альтернативные исследовательские веса v2/v3, отдельный CLI всех трёх биохимических целей v4, документация, агрегированные отчёты и синтетические примеры.

Публичный снимок подготовлен отдельно от рабочего окружения. Исходные медицинские таблицы, построчные прогнозы, исходные PDF/DOCX требований, списки обучающих ID, рабочие аккаунты, загруженные файлы, секреты, личные пути и внутренние инструкции не включены. Словарь признаков и единицы сохранены; его техническое описание source path переведено в относительный путь.

Публичные joblib-копии содержат те же fitted estimators, классы, признаки, thresholds и калибраторы. Удалены служебные ID и абсолютные пути, для v4 обновлён только хеш минимизированного словаря. Обновлены реестры доверенных публичных весов. На 27 синтетических панелях (включая неполный ввод) до/после сериализации проверено совпадение результатов всех семейств с численной точностью 1e-12 и сохранение статусов/правил. Повторное обучение и изменение выбора моделей не выполнялись.

MODEL_PUBLICATION.json связывает исходные исследовательские и публичные SHA-256. Исторические experiment locks/отчёты сохраняют исходные хеши и даты обучения: они не переименованы в новую фиксацию до внешнего теста. FILES_MANIFEST.json описывает текущий публикуемый состав. Не смешивайте исторические хеши исследования с публичным реестром весов.

Подробные результаты локальной проверки публичного снимка будут записаны в docs/PUBLIC_RELEASE_VERIFICATION.json. Проверка macOS/синтетических сценариев не является независимой клинической валидацией, юридическим допуском или проверкой чистой установки Windows. Обработка реальных идентифицируемых пациентов остаётся закрытой.
""", encoding="utf-8")
    start = target / "docs/00_START_HERE.md"
    start.write_text("""# Hema для жюри MedITron

Исследовательский публичный выпуск от 05.10.2026. Начните с [маршрута проверки](JURY_GUIDE.md) и [пошаговой установки macOS/Windows](06_LOCAL_SETUP.md). Все вычисления выполняются локально; веса включены.

Только синтетические или действительно обезличенные данные. Клинический допуск и обработка идентифицируемых реальных пациентов отсутствуют. Для тестов врача создайте вымышленную учётную запись по backend/AUTH.md.

Технические руководства: [структура](01_PROJECT_STRUCTURE.md), [модели](02_MODEL.md), [бэкенд](03_BACKEND.md), [фронтенд](04_FRONTEND.md), [рекомендации](05_RECOMMENDATIONS.md), [проверки](07_VERIFICATION.md), [безопасность](09_SECURITY_REMEDIATION.md), [гибрид v1/v4](10_HYBRID_MODEL.md). Отдельно сохранены [юридические шаблоны](legal/README.md).

Публичный состав включает реализацию, пояснения, агрегированные результаты и синтетические примеры. Исходные медицинские таблицы, служебные ID/пути/инструкции, базы, загруженные документы и секреты исключены. Для нового обучения источник требуется получить отдельно на законном основании. Подробнее — RELEASE_NOTES.md и THIRD_PARTY_NOTICES.md в корне.
""", encoding="utf-8")
    structure = target / "docs/01_PROJECT_STRUCTURE.md"
    text = structure.read_text("utf-8").split("<!-- generated inventory -->")[0]
    text = text.replace("Для отдельного исследовательского CLI; сайт остаётся v1", "Ферритин интегрирован отдельным выходом; B12/PLP доступны в CLI")
    text = text.replace("В workspace CSV/описания; в ZIP только продуктовые PDF, CSV исключён", "Исходные материалы отдельно; CSV/PDF требований в публичный выпуск не включены")
    text = text.replace("Внешние данные подготовлены для исследования, но не включены в обучение baseline_v1.", "Внешние данные не включены в обучение baseline_v1; NHANES использован отдельно для v4.")
    text += "\n\n## Публичный состав MedITron\n\nВнутренний контекст и исходные материалы кейса не опубликованы. Рабочий алгоритм и ограничения — docs/10_HYBRID_MODEL.md и RELEASE_NOTES.md. Реестр ниже относится к публичной копии; машинные проверки и манифест описываются отдельно.\n\n<!-- generated inventory -->\n\n| Путь | Назначение |\n|---|---|\n"
    inventory = sorted(p for p in target.rglob("*") if p.is_file())
    for path in inventory:
        text += f"| `{path.relative_to(target).as_posix()}` | {handoff.purpose(path)} |\n"
    # purpose expects the original root; use its basename fallback for new files.
    structure.write_text(text, encoding="utf-8")

    def valid_link(match, parent):
        label, url = match.group(1), html.unescape(match.group(2))
        if re.match(r"^(?:https?://|mailto:|#)", url):
            return match.group(0)
        path = (parent / url.split("#", 1)[0]).resolve()
        if path.exists():
            return match.group(0)
        return label + " (исходный файл отдельно)"

    for path in target.rglob("*.md"):
        if "vendor" in path.parts:
            continue
        text = path.read_text("utf-8")
        text = re.sub(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", lambda m: valid_link(m, path.parent), text)
        path.write_text(text, encoding="utf-8")
    handoff.ROOT = target
    for path in sorted((target / "docs").rglob("*.md")):
        text = path.read_text("utf-8")
        title = text.splitlines()[0].lstrip("# ")
        navigation = '<a href="' + ("../../" if path.parent.name == "legal" else "../") + 'START_HERE.html">Начало</a>'
        path.with_suffix(".html").write_text(handoff.document(title, handoff.render(text), navigation), encoding="utf-8")
    links = '<h1>Hema — MedITron</h1><p>Исследовательский выпуск для жюри. Только синтетические или действительно обезличенные данные.</p><ul>'
    for name in ["JURY_GUIDE", "06_LOCAL_SETUP", "01_PROJECT_STRUCTURE", "02_MODEL", "03_BACKEND", "04_FRONTEND", "05_RECOMMENDATIONS", "10_HYBRID_MODEL", "07_VERIFICATION", "09_SECURITY_REMEDIATION"]:
        title = (target / "docs" / (name + ".md")).read_text("utf-8").splitlines()[0].lstrip("# ")
        links += '<li><a href="docs/' + name + '.html">' + html.escape(title) + '</a></li>'
    links += '</ul><p>Для запуска установите Python 3.12 и используйте START_MAC.command или START_WINDOWS.bat.</p>'
    (target / "START_HERE.html").write_text(handoff.document("Hema — MedITron", links, '<a href="README.md">README</a>'), encoding="utf-8")
    print(json.dumps({"output": str(target), "files": sum(p.is_file() for p in target.rglob("*")), "models": models}, ensure_ascii=False))


if __name__ == "__main__":
    main()
