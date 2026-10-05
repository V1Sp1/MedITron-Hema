"""Load the trusted local champion bundle once and adapt research scores to reports."""

import hashlib
import io
import json
import math
from pathlib import Path
from threading import Lock

from .features import DICTIONARY_VERSION, FEATURES, ROOT
from .verdicts import ModelVerdict, verdict_context

DEFAULT_MODEL_BUNDLE = ROOT / "experiments/baseline_v1/models/selected.joblib"
CLASS_LABELS = {
    "no_anemia_no_deficiency": "Порог анемии не достигнут",
    "latent_deficiency": "Возможное дефицитное состояние без анемии",
    "iron_deficiency_anemia": "Возможная железодефицитная анемия",
    "B12_deficiency_anemia": "Возможная B12-дефицитная анемия",
    "B12_deficiency_no_anemia": "Возможный дефицит B12 без анемии",
    "folate_deficiency_anemia": "Возможная фолатодефицитная анемия",
    "folate_deficiency_no_anemia": "Возможный дефицит фолатов без анемии",
    "B6_deficiency": "Возможный дефицит B6",
    "copper_deficiency": "Возможный дефицит меди",
    "inflammation_anemia": "Возможная анемия на фоне воспаления",
    "mixed_deficiency": "Возможно сочетание дефицитов",
    "anemia_other": "Причина анемии требует уточнения",
}
DEFICIT_LABELS = {"iron": "Железо", "B12": "Витамин B12", "folate": "Фолаты",
                  "B6": "Витамин B6", "copper": "Медь"}
TARGET_CODES = {"iron_deficiency": "iron", "B12_deficiency": "B12",
                "folate_deficiency": "folate", "B6_deficiency": "B6", "copper_deficiency": "copper"}


class ModelUnavailable(RuntimeError):
    pass


class ModelService:
    def __init__(self, path: Path, trusted_sha256: str | None = None):
        self.bundle = None
        self.version = None
        self.lock = Lock()
        self.error = None
        try:
            # joblib is executable serialization: only administrator-selected local weights.
            import joblib
            from ml_baselines.predict import predict
            self.infer = predict
            path = Path(path)
            if path.stat().st_size > 128 * 1024 * 1024:
                raise ValueError("Model bundle too large")
            # Validate the exact bytes BEFORE executable deserialization; use those
            # same bytes afterwards, avoiding a path replacement between checks.
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            approved = json.loads((Path(__file__).parent / "data/model_trust.json").read_text("utf-8"))
            if digest != trusted_sha256 and digest not in approved["sha256"].values():
                self.error = "untrusted_weights"
                raise ValueError("Unapproved executable model serialization")
            bundle = joblib.load(io.BytesIO(raw))
            self._validate_bundle(bundle)
            self.bundle = bundle
            self.family = bundle.get("model_family", "baseline_v1")
            # Verify the installed inference runtime and both routes before declaring readiness.
            for panel in ("primary", "cbc"):
                self._validate_scores(self.infer(bundle, {"age_years": 42, "sex": "F", "hemoglobin": 108}, panel))
            self.version = "hema-" + self.family.replace("_", "-") + "-" + digest[:12]
        except Exception:
            self.bundle = None
            self.error = self.error or "load_failed"

    @staticmethod
    def _validate_bundle(bundle):
        if (bundle["schema_version"] != "1.0" or bundle["features"] != list(FEATURES)
                or bundle["dictionary_sha256"] != DICTIONARY_VERSION):
            raise ValueError("Incompatible model schema or dictionary")
        if bundle.get("model_family", "baseline_v1") not in {"baseline_v1", "robust_v2", "calibrated_v3"}:
            raise ValueError("Unknown model family")
        if set(bundle["labels"]) != {"anemia_class", "inflammation_anemia", *TARGET_CODES}:
            raise ValueError("Incomplete model targets")
        if set(bundle["labels"]["anemia_class"]) != set(CLASS_LABELS):
            raise ValueError("Unknown class labels")
        for target, labels in bundle["labels"].items():
            if target != "anemia_class" and labels != [0, 1]:
                raise ValueError("Invalid binary labels")
            for panel in ("primary", "cbc"):
                name = bundle["selected"][target][panel]
                if set(bundle["models"][(name, target)].classes_) != set(labels):
                    raise ValueError("Model classes differ")
                bundle["configs"][name]
        if bundle.get("model_family") == "calibrated_v3":
            from ml_baselines.calibration import ScoreCalibrator
            from ml_baselines.predict import calibration_metadata
            expected = {(target, panel) for target in bundle["labels"] for panel in ("primary", "cbc")}
            if set(bundle.get("calibrators", {})) != expected:
                raise ValueError("Incomplete calibration heads")
            for (target, _), calibrator in bundle["calibrators"].items():
                if (not isinstance(calibrator, ScoreCalibrator) or calibrator.method not in {"identity", "temperature", "sigmoid_logit"}
                    or not math.isfinite(calibrator.slope) or calibrator.slope <= 0 or not math.isfinite(calibrator.intercept)
                    or target == "anemia_class" and calibrator.method == "sigmoid_logit"):
                    raise ValueError("Invalid calibrator")
            metadata = calibration_metadata(bundle)
            if (bundle["calibration"]["status"] != metadata["status"] or bundle["calibration"]["clinicalValidated"] is not False
                or bundle["calibrated"] != (metadata["status"] == "all_heads")):
                raise ValueError("Misleading calibration metadata")
        elif bundle.get("calibrators"):
            raise ValueError("Unexpected calibration in legacy family")

    @staticmethod
    def _validate_scores(result):
        if set(result["scores"]) != {"anemia_class", "inflammation_anemia", *TARGET_CODES}:
            raise ValueError("Incomplete prediction")
        for score in result["scores"].values():
            values = list(score["values"].values())
            if (not values or any(not math.isfinite(v) or not 0 <= v <= 1 for v in values)
                    or not math.isclose(sum(values), 1, abs_tol=1e-6)):
                raise ValueError("Invalid scores")

    def status(self):
        calibration = None
        if self.bundle is not None:
            from ml_baselines.predict import calibration_metadata
            calibration = calibration_metadata(self.bundle)
        applied = bool(calibration and calibration["appliedHeads"])
        return {"connected": self.bundle is not None, "modelVersion": self.version,
                "state": "ready" if self.bundle is not None else "unavailable", "errorCode": self.error,
                "supportedMethods": ["case_hemoglobin_rule", "local_champion_models"],
                "researchOnly": True, "calibrated": bool(calibration and calibration["status"] == "all_heads"),
                "calibrationApplied": applied, "calibration": calibration, "decisionThreshold": .5,
                "modelFamily": self.family if self.bundle is not None else None,
                "unitDictionaryVersion": DICTIONARY_VERSION,
                "selectedModels": self.bundle["selected"] if self.bundle is not None else {},
                "message": ("Локальные исследовательские модели подключены; часть оценок откалибрована на данных кейса, клиническая достоверность не установлена."
                            if applied else "Локальные исследовательские модели подключены; оценки не откалиброваны.")
                           if self.bundle is not None else "Не удалось загрузить модель. Проверьте веса и зависимости."}

    def apply(self, report):
        if self.bundle is None:
            raise ModelUnavailable("Модель недоступна. Проверьте локальные веса и зависимости сервера.")
        try:
            with self.lock:
                result = self.infer(self.bundle, report["inputs"])
            self._validate_scores(result)
        except Exception as exc:
            raise ModelUnavailable("Не удалось получить прогноз модели. Повторите запрос после проверки сервера.") from exc
        scores = result["scores"]
        report.update(modelConnected=True, modelVersion=self.version, source="api",
                      researchOnly=True, calibrated=result.get("calibrated", False), modelPanel=result["panel"],
                      calibrationApplied=result.get("calibration_applied", False), calibration=result.get("calibration"),
                      scoreMeaning=("calibrated_research_score" if result.get("calibrated") else "partially_calibrated_research_score")
                                   if result.get("calibration_applied") else "uncalibrated_model_score",
                      modelFamily=self.bundle.get("model_family", "baseline_v1"),
                      classScoresConditionedOnHb=result.get("class_scores_conditioned_on_hb", False),
                      decisionThreshold=.5, selectedModels={t: s["model"] for t, s in scores.items()},
                      method="Порог Hb из кейса СУ и локальные исследовательские модели " + self.bundle.get("model_family", "baseline_v1"),
                      warnings=list(result["warnings"]))
        report.update(deficiencyScores=[], anemiaScores=[])
        data = report["dataSufficiency"]
        if data["level"] == "insufficient":
            report.update(modelVerdict=ModelVerdict().model_dump(), modelDecisionState="suppressed_sparse",
                          decisionReasonCodes=list(data["reasonCodes"]),
                          prediction={"code": None, "label": "Недостаточно анализов для оценки причины",
                                      "hiddenDeficitLabel": "Дефициты не оценены: данных недостаточно."})
            report["warnings"].append(data["summary"])
            return report
        classes = scores["anemia_class"]["values"]
        kind = max(classes, key=classes.get)
        deficits = [code for target, code in TARGET_CODES.items() if scores[target]["values"]["1"] >= .5]
        inflammation = scores["inflammation_anemia"]["values"]["1"] >= .5
        verdict = ModelVerdict(anemiaClass=kind, deficits=deficits, inflammation=inflammation)
        report["modelVerdict"] = verdict.model_dump()
        context = verdict_context(report, data)
        # Abstention is a prototype policy; .5 is not a validated clinical confidence bound.
        reasons = []
        if context["modelState"] == "inconsistent":
            reasons.extend(["inconsistent_targets_or_hb", *context["reasonCodes"]])
        if kind == "latent_deficiency" and deficits != ["iron"]:
            reasons.append("inconsistent_targets")
        if classes[kind] < .5:
            reasons.append("ambiguous_class_scores")
        if kind in {"no_anemia_no_deficiency", "anemia_other"} and data["level"] != "broader":
            reasons.append("incomplete_exclusion_panel")
        if reasons:
            verdict.status = "uncertain"
            report["modelVerdict"] = verdict.model_dump()
        report["modelDecisionState"] = verdict_context(report, data)["modelState"]
        report["decisionReasonCodes"] = reasons
        evaluated = report["modelDecisionState"] == "evaluated"
        report["prediction"] = {"code": kind if evaluated else None,
                                "label": CLASS_LABELS[kind] if evaluated else "Оценка причины неопределённа",
                                "hiddenDeficitLabel": ("Модель допускает: " + ", ".join(DEFICIT_LABELS[d] for d in deficits))
                                if evaluated and deficits else "Дефициты требуют уточнения; низкая оценка модели их не исключает."}
        report["deficiencies"] = deficits if evaluated else None
        report["deficiencyProbabilities"] = [{"code": code, "label": DEFICIT_LABELS[code],
                                               "probability": scores[target]["values"]["1"]}
                                              for target, code in TARGET_CODES.items()]
        report["anemiaProbabilities"] = [{"code": code, "label": CLASS_LABELS[code], "probability": value}
                                         for code, value in classes.items()] if report["anemia"] else []
        if not evaluated:
            report["warnings"].append("Вывод о причине неопределён или противоречив; специфические рекомендации подавлены.")
        if data["level"] != "broader":
            report["warnings"].append(data["summary"])
        report["warnings"].append("Оценки модели 0–1 не являются проверенной вероятностью заболевания. Низкие значения не исключают дефицит.")
        report["deficiencyScores"] = [{"code": item["code"], "label": item["label"], "score": item["probability"]}
                                      for item in report["deficiencyProbabilities"]]
        report["anemiaScores"] = [{"code": item["code"], "label": item["label"], "score": item["probability"]}
                                 for item in report["anemiaProbabilities"]]
        return report
