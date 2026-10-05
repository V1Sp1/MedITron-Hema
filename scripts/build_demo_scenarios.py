"""Fictional panels evaluated by the trusted local model, never source patients.
Run: .venv/bin/python scripts/build_demo_scenarios.py
No training, API requests, or database writes; outputs are not clinical truth.
"""
import csv
import io
import json
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.features import FEATURES
from backend.model_service import DEFAULT_MODEL_BUNDLE, ModelService
from backend.recommendations import phrases_for_report
from backend.screening import screen

# Manually authored values, never copied from training or patient records.
NORMAL = {
    "age_years": 42, "sex": "F", "hemoglobin": 135, "RBC": 4.5,
    "hematocrit": 40, "MCV": 89, "MCH": 30, "MCHC": 337, "RDW": 13,
    "platelets": 250, "WBC": 6, "reticulocytes": 1, "ferritin": 65,
    "serum_iron": 16, "transferrin": 2.5, "TIBC": 63, "UIBC": 47,
    "TSAT": 25, "sTfR": 1.5, "Ret_He": 31, "vitamin_B12": 450,
    "active_B12": 80, "MMA": .15, "homocysteine": 9, "folate": 10,
    "vitamin_B6": 45, "copper": 17, "ceruloplasmin": .25, "CRP": 1,
    "ESR": 8, "creatinine": 70, "eGFR": 100, "TSH": 2, "albumin": 43,
    "LDH": 180, "indirect_bilirubin": 8, "haptoglobin": 1.2,
}


def panel(**overrides):
    return {**NORMAL, **overrides}


# Labels describe inputs; neither the verdict nor the numeric scores are assigned.
SCENARIOS = [
    ("iron", "Железо: расширенная панель",
     "Низкие показатели обмена железа и микроцитарная картина; доступна расширенная панель.",
     panel(hemoglobin=105, RBC=4.3, hematocrit=31, MCV=72, MCH=24, MCHC=339,
           RDW=18, ferritin=6, serum_iron=4, transferrin=3.8, TIBC=80,
           UIBC=76, TSAT=5, sTfR=5, Ret_He=22)),
    ("B12", "B12: анемия и расширенная панель",
     "Низкие общий и активный B12, повышенные функциональные маркеры и макроцитарная картина.",
     panel(age_years=65, hemoglobin=95, RBC=2.4, hematocrit=28, MCV=112,
           MCH=39, MCHC=335, RDW=21, vitamin_B12=60, active_B12=8, MMA=2,
           homocysteine=45, LDH=550, indirect_bilirubin=22, reticulocytes=.5)),
    ("folate", "Фолаты: расширенная панель",
     "Низкий уровень фолатов, повышенный гомоцистеин и макроцитарная картина.",
     panel(hemoglobin=105, RBC=3.09, hematocrit=32.4, MCV=105, MCH=34,
           MCHC=324, folate=1.5, homocysteine=28)),
    ("B6", "B6: расширенная панель",
     "Низкий B6 при микроцитарной картине. Активный B12 отсутствует, общий B12 указан.",
     panel(hemoglobin=105, RBC=4.3, hematocrit=31, MCV=72, MCH=24,
           MCHC=339, RDW=18, vitamin_B6=2, active_B12=None)),
    ("copper", "Медь: расширенная панель",
     "Низкие медь и церулоплазмин; Hb ниже порога.",
     panel(hemoglobin=110, RBC=3.67, hematocrit=33, MCV=90, MCH=30,
           MCHC=333, copper=4, ceruloplasmin=.1)),
    ("mixed", "Железо и B12: сочетание признаков",
     "Одновременно снижены показатели обмена железа и B12; повышен RDW.",
     panel(hemoglobin=95, RBC=3.1, hematocrit=28, MCV=90, MCH=31, MCHC=339,
           RDW=20, ferritin=5, serum_iron=4, TIBC=67, UIBC=63, TSAT=6,
           transferrin=2.7, sTfR=5, vitamin_B12=90, active_B12=14,
           MMA=1.1, homocysteine=32)),
    ("inflammation", "Повышенные маркеры воспаления",
     "Повышены CRP, СОЭ и ферритин; сывороточное железо и трансферрин снижены.",
     panel(hemoglobin=105, RBC=3.5, hematocrit=31.5, MCV=90, MCH=30,
           MCHC=333, CRP=55, ESR=48, ferritin=250, serum_iron=6,
           transferrin=1.5, TIBC=38, UIBC=32, TSAT=16, albumin=32)),
    ("other", "Анемия и изменённые показатели почек",
     "Hb ниже порога, креатинин повышен, СКФ снижена. Это не установленная причина анемии.",
     panel(age_years=65, hemoglobin=100, RBC=3.3, hematocrit=30,
           creatinine=200, eGFR=28)),
    ("normal", "Hb выше порога: расширенная панель",
     "Показаны все 35 лабораторных показателей; модель оценивает возможные дефициты независимо от Hb.",
     panel()),
    ("B12_boundary", "B12 при Hb = 120: неоднозначная оценка",
     "Hb точно равен женскому порогу. Данные по B12 изменены, но оценки модели недостаточны для конкретного причинного вывода.",
     panel(hemoglobin=120, RBC=4, hematocrit=36, MCV=90, MCH=30, MCHC=333,
           vitamin_B12=100, active_B12=15, MMA=1.2, homocysteine=30)),
    ("male_boundary", "Мужчина, Hb = 130: пограничный случай",
     "Hb точно равен мужскому порогу; наличие анемии определяется строгим сравнением, а дефициты оцениваются отдельно.",
     panel(sex="M", hemoglobin=130, RBC=4.33, hematocrit=39,
           MCV=90, MCH=30, MCHC=333)),
    ("uncertain", "Расширенная панель: причина неопределённа",
     "Изменены ретикулоциты и маркеры гемолиза. Оценки отображаются, конкретная причина не назначается.",
     panel(hemoglobin=105, RBC=3.5, hematocrit=31, reticulocytes=5,
           LDH=650, indirect_bilirubin=35, haptoglobin=.1)),
    ("conflict", "Расхождение модели с правилом Hb",
     "Признаки снижения запасов железа при Hb = 120. Классификатор и правило Hb расходятся; специфический вывод подавляется.",
     panel(hemoglobin=120, RBC=4.6, hematocrit=35.9, MCV=78, MCH=26,
           MCHC=334, RDW=17, ferritin=6, serum_iron=4, transferrin=3.8,
           TIBC=80, UIBC=76, TSAT=5, sTfR=5, Ret_He=22, active_B12=None)),
    ("cbc", "Только общий анализ крови",
     "Девять показателей ОАК. Нет панели обмена железа, витаминов и маркеров воспаления; ограничения видны в отчёте.",
     {key: value for key, value in panel(hemoglobin=105, RBC=4.3,
      hematocrit=31, MCV=72, MCH=24, MCHC=339, RDW=18).items()
      if key in {"age_years", "sex", "hemoglobin", "RBC", "hematocrit", "MCV",
                 "MCH", "MCHC", "RDW", "platelets", "WBC"}}),
    ("sparse", "Мало данных: только гемоглобин",
     "Возраст, пол и один лабораторный показатель. Вывод ограничивается правилом Hb и списком недостающих данных.",
     {"age_years": 42, "sex": "F", "hemoglobin": 108}),
]


def build_examples(service=None):
    service = service or ModelService(DEFAULT_MODEL_BUNDLE)
    if not service.status()["connected"]:
        raise RuntimeError("Trusted local model required; no synthetic-score fallback")
    examples = []
    for key, label, description, inputs in SCENARIOS:
        computed = service.apply(screen(inputs, "doctor"))
        reports = {}
        for role in ("patient", "doctor"):
            report = deepcopy(computed)
            report["audience"] = role
            recommendations = phrases_for_report(report)
            report.update(
                source="demo", simulation=False, demoModelComputed=True,
                demoScenarioId=key, demoScenarioLabel=label, demoDescription=description,
                demoProvenance={"inputSource": "authored_synthetic_panel",
                                "predictionSource": "trusted_local_model",
                                "modelVersion": service.version},
                recommendations=recommendations["items"],
                recommendationConclusions=recommendations["conclusions"],
                recommendationVersion=recommendations["phraseFileVersion"],
                recommendationStatus=recommendations["status"],
                clinicalReviewStatus=recommendations["clinicalReviewStatus"],
                recommendationVerdictContext=recommendations["verdictContext"],
                dataSufficiency=recommendations["dataSufficiency"],
                insufficientData=recommendations["insufficientData"],
                warnings=list(dict.fromkeys([
                    "Полностью вымышленные анализы. Сохранён настоящий расчёт локальной исследовательской модели; это пример работы программы, а не данные пациента.",
                    *[recommendations["dataSufficiency"]["summary"] if warning == computed["dataSufficiency"]["summary"] else warning for warning in computed["warnings"]],
                    *recommendations["warnings"]])),
            )
            reports[role] = report
        examples.append({"id": key, "label": label, "reports": reports})
    return examples


def main():
    examples = build_examples()
    target = ROOT / "frontend/src/demo-scenarios.js"
    payload = json.dumps(examples, ensure_ascii=False, indent=2, allow_nan=False)
    target.write_text("// Generated by scripts/build_demo_scenarios.py; do not edit by hand.\n"
                      + "export const scenarios = " + payload + ";\n", encoding="utf-8")
    directory = ROOT / "examples/full-demo"
    directory.mkdir(parents=True, exist_ok=True)
    table = io.StringIO(newline="")
    writer = csv.DictWriter(table, fieldnames=list(FEATURES))
    writer.writeheader()
    for example in examples:
        writer.writerow(example["reports"]["doctor"]["inputs"])
    (directory / "input_panels.csv").write_text(table.getvalue(), encoding="utf-8-sig")
    (directory / "reports.json").write_text(payload + "\n", encoding="utf-8")
    version = examples[0]["reports"]["doctor"]["modelVersion"]
    guide = (f"# Полные вымышленные примеры Hema\n\n{len(examples)} панелей × две роли. Модель: {version}.\n\n"
             "Все значения созданы вручную. Здесь нет исходных записей пациентов.\n"
             "Отчёты — реальные расчёты модели на этих панелях, а не эталонные диагнозы.\n"
             "Модель и фразы остаются исследовательскими. Числа 0–1 не являются проверенными вероятностями заболеваний.\n\n"
             "1. Откройте сайт → выберите роль → «Посмотреть пример». Все сценарии доступны в списке.\n"
             "2. Для повторного расчёта импортируйте input_panels.csv и выберите запись. Порядок приведён ниже.\n"
             "3. reports.json содержит входы, числовые оценки, ограничения и фразы для пациента и врача.\n"
             "4. Выбор примера не запускает API: показывает сохранённый расчёт. Для повторения с теми же весами нажмите «Рассчитать».\n"
             "5. Пустые ячейки CSV означают отсутствующий анализ, а не ноль. Единицы — из словаря проекта.\n\n"
             + "\n".join(f"{i}. {item['label']} ({item['id']})" for i, item in enumerate(examples, 1))
             + "\n\nОбновление: .venv/bin/python scripts/build_demo_scenarios.py, затем сборка фронтенда.\n")
    (directory / "README.md").write_text(guide, encoding="utf-8")
    with zipfile.ZipFile(directory / "hema-full-examples.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("README.md", "input_panels.csv", "reports.json"):
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 10, 5, 0, 0, 0)),
                             (directory / name).read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    print(f"Generated {len(examples)} fictional panels / {len(examples)*2} reports with {version}")
    for item in examples:
        r = item["reports"]["doctor"]
        print(item["id"], r["modelDecisionState"], r["prediction"]["code"], r["dataSufficiency"]["laboratoryCount"])


if __name__ == "__main__":
    main()
