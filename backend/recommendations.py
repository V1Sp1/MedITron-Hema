"""Versioned editorial phrases selected by explicit decisions and input coverage."""

import json
from importlib.resources import files
from pathlib import Path

from .data_sufficiency import assess_data
from .features import FEATURES, validate_inputs
from .verdicts import ModelVerdict, verdict_context

KINDS = {"conclusion", "next_step", "data_gap"}
CONDITIONS = {"anemia", "deficit", "maxDeficits", "mixed", "inflammation", "anemiaClass",
              "nonAnemicDeficit", "presentAll", "presentAny", "missingAny", "missingGroupsAny", "dataLevels", "modelStates"}
DEFICIT_NAMES = {"iron": "железо", "B12": "витамин B12", "folate": "фолаты", "B6": "витамин B6", "copper": "медь"}


def phrase_catalog(audience: str) -> dict:
    if audience not in {"patient", "doctor"}:
        raise ValueError("Неизвестная роль.")
    content = json.loads(files("backend").joinpath(f"data/recommendations_{audience}.json").read_text("utf-8"))
    if content.get("audience") != audience or content.get("clinicalReviewStatus") != "draft" or not content.get("version"):
        raise ValueError("Неверная версия/роль каталога фраз.")
    items = content.get("items")
    if not isinstance(items, list) or len(items) != 20:
        raise ValueError("Каталог роли должен содержать 20 фраз.")
    ids = set()
    for item in items:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or item["id"] in ids
                or item.get("kind") not in KINDS or type(item.get("priority")) is not int
                or not isinstance(item.get("text"), str) or not item["text"].strip()
                or not isinstance(item.get("when"), dict) or set(item["when"]) - CONDITIONS
                or not isinstance(item.get("sourceIds"), list) or any(key not in content["sources"] for key in item["sourceIds"])
                or not isinstance(item.get("suggestedFeatures"), list) or any(key not in FEATURES for key in item["suggestedFeatures"])):
            raise ValueError("Некорректная структура фразы.")
        # Validate controlled operands instead of executing expressions from a file.
        when = item["when"]
        for key in ("anemia", "mixed", "inflammation", "nonAnemicDeficit"):
            if key in when and type(when[key]) is not bool:
                raise ValueError(f"Некорректное условие {key}.")
        if "deficit" in when and when["deficit"] not in DEFICIT_NAMES:
            raise ValueError("Неизвестный дефицит в правиле.")
        if "anemiaClass" in when:
            ModelVerdict(anemiaClass=when["anemiaClass"])
        if "maxDeficits" in when and (type(when["maxDeficits"]) is not int or not 1 <= when["maxDeficits"] <= 5):
            raise ValueError("Неверное число дефицитов в правиле.")
        allowed = {"presentAll": set(FEATURES), "presentAny": set(FEATURES), "missingAny": set(FEATURES),
                   "missingGroupsAny": {"iron", "B12", "folate", "B6", "copper"},
                   "dataLevels": {"insufficient", "limited", "broader"},
                   "modelStates": {"not_connected", "missing_verdict", "evaluated", "uncertain", "out_of_scope", "inconsistent", "suppressed_sparse"}}
        for key, values in allowed.items():
            if key in when and (not isinstance(when[key], list) or not when[key]
                                or any(not isinstance(value, str) or value not in values for value in when[key])):
                raise ValueError(f"Некорректное условие {key}.")
        # Only one known substitution, no dynamic code or arbitrary formatting.
        rendered = item["text"].replace("{deficits}", "")
        if "{" in rendered or "}" in rendered:
            raise ValueError("Неизвестная подстановка в тексте фразы.")
        ids.add(item["id"])
    return content


def _matches(when: dict, report: dict, inputs: dict, data: dict, context: dict) -> bool:
    if "anemia" in when and report.get("anemia") is not when["anemia"]:
        return False
    if "dataLevels" in when and data["level"] not in when["dataLevels"]:
        return False
    if "modelStates" in when and context["modelState"] not in when["modelStates"]:
        return False
    if "presentAll" in when and not all(inputs[key] is not None for key in when["presentAll"]):
        return False
    if "presentAny" in when and not any(inputs[key] is not None for key in when["presentAny"]):
        return False
    if "missingAny" in when and not any(inputs[key] is None for key in when["missingAny"]):
        return False
    if "missingGroupsAny" in when:
        missing = {group["id"] for group in data["groups"] if not group["covered"]}
        if not missing.intersection(when["missingGroupsAny"]):
            return False
    model_conditions = {"deficit", "maxDeficits", "mixed", "inflammation", "anemiaClass", "nonAnemicDeficit"}
    if model_conditions.intersection(when) and context["modelState"] != "evaluated":
        return False
    deficits = context["deficits"]
    if "deficit" in when and when["deficit"] not in deficits:
        return False
    if "maxDeficits" in when and len(deficits) > when["maxDeficits"]:
        return False
    if "mixed" in when and (len(deficits) >= 2 or context["anemiaClass"] == "mixed_deficiency") is not when["mixed"]:
        return False
    if "inflammation" in when and context["inflammation"] is not when["inflammation"]:
        return False
    if "anemiaClass" in when and context["anemiaClass"] != when["anemiaClass"]:
        return False
    if "nonAnemicDeficit" in when:
        latent = report.get("anemia") is False and (bool(deficits) or context["anemiaClass"] == "latent_deficiency")
        if latent is not when["nonAnemicDeficit"]:
            return False
    return True


def _legacy_phrases(report: dict, path: Path) -> dict:
    """Preserve the previously documented reviewed-file format for explicit overrides."""
    content = json.loads(path.read_text("utf-8"))
    if not isinstance(content, dict) or content.get("reviewed") is not True or not isinstance(content.get("version"), str) or not content["version"]:
        raise ValueError("Файл фраз должен иметь version и reviewed: true.")
    items = content.get("items")
    if not isinstance(items, list):
        raise ValueError("items должен быть массивом фраз.")
    selected, ids = [], set()
    for item in items:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or item["id"] in ids
                or not isinstance(item.get("text"), str) or not item["text"].strip()
                or not isinstance(item.get("audience"), str) or item["audience"] not in {"doctor", "patient", "both"}
                or not isinstance(item.get("when"), dict) or set(item["when"]) - {"anemia"}
                or ("anemia" in item["when"] and type(item["when"]["anemia"]) is not bool)):
            raise ValueError("Некорректная структура клинической фразы.")
        ids.add(item["id"])
        if item["audience"] in {report["audience"], "both"} and ("anemia" not in item["when"] or item["when"]["anemia"] is report.get("anemia")):
            selected.append({"id": item["id"], "text": item["text"]})
    return {"items": selected, "phraseFileVersion": content["version"], "status": "configured", "warnings": []}


def phrases_for_report(report: dict, path: Path | None = None, *, simulation: bool = False) -> dict:
    audience = report["audience"]
    inputs = validate_inputs(report["inputs"], require_core=False)
    data = assess_data(inputs, audience)
    catalog = phrase_catalog(audience)
    context = verdict_context(report, data, simulation=simulation)
    grouped = {kind: [] for kind in KINDS}
    for item in sorted(catalog["items"], key=lambda row: (-row["priority"], row["id"])):
        if not _matches(item["when"], report, inputs, data, context):
            continue
        rendered = {key: item[key] for key in ("id", "text", "kind", "topic", "suggestedFeatures", "sourceIds")}
        names = ", ".join(DEFICIT_NAMES[key] for key in context["deficits"]) or "компоненты не уточнены"
        rendered["text"] = rendered["text"].replace("{deficits}", names)
        rendered["reasonCodes"] = [item["topic"], *data["reasonCodes"]] if item["kind"] == "data_gap" else [item["topic"]]
        if item["topic"] == "model_limited":
            rendered["reasonCodes"] = list(dict.fromkeys(
                [item["topic"], *context["reasonCodes"], *report.get("decisionReasonCodes", [])]))
        # Propose only missing inputs, not blanket orders for all tests.
        rendered["suggestedFeatures"] = [key for key in item["suggestedFeatures"] if inputs[key] is None]
        if inputs["vitamin_B12"] is not None or inputs["active_B12"] is not None:
            rendered["suggestedFeatures"] = [key for key in rendered["suggestedFeatures"]
                                             if key not in {"vitamin_B12", "active_B12"}]
        grouped[item["kind"]].append(rendered)
    result = {"items": grouped["next_step"][:4], "conclusions": grouped["conclusion"][:3],
              "dataSufficiency": data,
              "insufficientData": {"active": data["level"] == "insufficient", "hasGaps": bool(grouped["data_gap"]),
                                   "summary": data["summary"], "items": grouped["data_gap"][:4]},
              "verdictContext": context, "phraseFileVersion": catalog["version"], "status": "draft",
              "clinicalReviewStatus": "draft", "simulation": simulation,
              "warnings": ["Часть текстов уточнена медицинской командой. Каталоги и правила достаточности остаются черновиком; клиническая валидация не выполнена."]}
    if path is not None:
        legacy = _legacy_phrases(report, path)
        legacy["warnings"] = [*result["warnings"], *legacy["warnings"]]
        result.update(legacy)
    return result
