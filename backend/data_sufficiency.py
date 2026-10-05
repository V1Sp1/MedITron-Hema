"""Editorial coverage policy, not calibrated model confidence or diagnosis."""

import json
from importlib.resources import files

from .features import LAB_KEYS, validate_inputs

POLICY = json.loads(files("backend").joinpath("data/sufficiency_policy.json").read_text("utf-8"))


def assess_data(inputs: dict, audience: str) -> dict:
    if audience not in {"patient", "doctor"}:
        raise ValueError("Неизвестная роль.")
    inputs = validate_inputs(inputs, require_core=False)
    available = [key for key in LAB_KEYS if inputs[key] is not None]
    essential = [key for key in ("age_years", "sex", "hemoglobin") if inputs[key] is None]
    cbc_missing = [key for key in POLICY["cbcContext"] if inputs[key] is None]
    groups = []
    for group in POLICY["groups"]:
        covered = any(all(inputs[key] is not None for key in alternative) for alternative in group["alternatives"])
        groups.append({"id": group["id"], "covered": covered,
                       "available": [key for key in group["features"] if inputs[key] is not None],
                       "initialTestAlternatives": group["alternatives"] if not covered else []})
    reasons = []
    if essential:
        reasons.append("missing_essential_inputs")
    if len(available) < POLICY["minimumLaboratoryValues"]:
        reasons.append("few_laboratory_values")
    if cbc_missing:
        reasons.append("incomplete_cbc_context")
    covered = {group["id"] for group in groups if group["covered"]}
    if essential or len(available) < POLICY["minimumLaboratoryValues"]:
        level = "insufficient"
    elif cbc_missing or not set(POLICY["broaderContextGroups"]) <= covered:
        level = "limited"
    else:
        level = "broader"
    count = len(available)
    summary = {
        "patient": {"insufficient": f"Данных мало: указано {count} из 35 лабораторных показателей. Причину состояния и отсутствие дефицитов по такой панели подтвердить нельзя.",
                    "limited": f"Указано {count} из 35 лабораторных показателей. Панель неполная; выводы о причинах и дефицитах ограничены.",
                    "broader": f"Указано {count} из 35 лабораторных показателей; доступны базовый контекст ОАК и часть анализов на дефициты. Это не гарантирует точность вывода."},
        "doctor": {"insufficient": f"Ограниченный объём данных: {count}/35 лабораторных признаков. Этиологическое заключение и исключение дефицитов не поддерживаются политикой прототипа.",
                   "limited": f"Панель {count}/35: часть диагностических групп отсутствует. Сохраняйте неопределённость при дифференциальной оценке.",
                   "broader": f"Панель {count}/35 содержит Hb, MCV, MCH и маркеры железа/B12/фолатов. Покрытие не является оценкой калибровки модели."}}
    return {"level": level, "summary": summary[audience][level], "laboratoryCount": count,
            "laboratoryTotal": len(LAB_KEYS), "availableFeatures": available,
            "missingFeatures": [key for key in LAB_KEYS if inputs[key] is None],
            "missingEssential": essential, "missingCBCContext": cbc_missing,
            "canAssessAnemia": not essential, "groups": groups, "reasonCodes": reasons,
            "policyVersion": POLICY["version"], "clinicalValidated": False,
            "minimumLaboratoryValues": POLICY["minimumLaboratoryValues"],
            "modelConfidence": None}
