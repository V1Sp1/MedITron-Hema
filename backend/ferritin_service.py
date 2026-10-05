"""Separate low-ferritin research output; never feeds clinical verdicts or phrases."""

import math
from pathlib import Path
from threading import Lock

from .features import ROOT

DEFAULT_FERRITIN_BUNDLE = ROOT / "experiments/biochemical_v4/models/selected.joblib"
PREGNANCY_STATUSES = {"unknown", "not_pregnant", "pregnant"}
LIMITATION = "Прогноз низкого ферритина — отдельная исследовательская оценка, не диагноз причины анемии. Низкая оценка не исключает дефицит."


class FerritinService:
    def __init__(self, path=DEFAULT_FERRITIN_BUNDLE):
        self.bundle = None
        self.version = None
        self.error = None
        self.enabled = path is not None
        self.lock = Lock()
        if not self.enabled:
            return
        try:
            from ml_baselines.biochemical_predict import load_candidate
            path = Path(path)
            if path.stat().st_size > 128 * 1024 * 1024:
                raise ValueError("Ferritin bundle too large")
            bundle = load_candidate(path)
            for route in ("cbc", "extended"):
                if bundle["validation"][f"low_ferritin/{route}"]["passed"] is not True:
                    raise ValueError("Ferritin external gate did not pass")
            # Version is tied to the verified serialization, also checked by the loader.
            self.version = "hema-biochemical-v4-" + bundle['loaded_sha256'][:12]
            self.bundle = bundle
            for extra in ({}, {"CRP": 3.}):
                result = self.evaluate({"age_years": 40, "sex": "F", "hemoglobin": 110.,
                    "RBC": 4., "hematocrit": 33., "MCV": 82., "MCH": 27.5, **extra}, "not_pregnant")
                if result["status"] != "research_prediction":
                    raise ValueError("Ferritin runtime check failed")
        except Exception:
            self.bundle = None
            self.version = None
            self.error = "load_failed"

    def status(self):
        return {"connected": self.bundle is not None, "modelFamily": "biochemical_v4",
                "modelVersion": self.version, "state": "ready" if self.bundle is not None else
                "unavailable" if self.enabled else "disabled", "errorCode": self.error,
                "endpoint": "low_ferritin", "researchOnly": True, "clinicalValidated": False,
                "scope": {"sex": "F", "minimumAge": 18, "maximumAge": 49,
                          "knownPregnancyStatusRequiredThroughAge": 44},
                "minimumSupportedLabs": 5}

    def evaluate(self, inputs, pregnancy="unknown"):
        if pregnancy not in PREGNANCY_STATUSES:
            raise ValueError("Invalid pregnancy status")
        common = {"endpoint": "low_ferritin", "label": "Прогноз низкого ферритина",
                  "modelFamily": "biochemical_v4", "modelVersion": self.version,
                  "researchOnly": True, "clinicalValidated": False, "unit": "µg/L",
                  "markerUsedAsPredictor": False, "pregnancyStatus": pregnancy}
        marker = inputs.get("ferritin")
        if marker is not None:
            return {**common, "status": "observed", "label": "Ферритин: результат анализа",
                    "observedValue": marker, "message": "Ферритин измерен: показан результат анализа, модельный прогноз не выполнялся."}
        if not self.enabled:
            return {**common, "status": "disabled", "message": "Прогноз ферритина отключён."}
        if self.bundle is None:
            return {**common, "status": "unavailable", "reasonCodes": ["model_unavailable"],
                    "message": "Прогноз ферритина недоступен: не удалось загрузить проверенные веса."}
        age, sex = inputs.get("age_years"), inputs.get("sex")
        if age is None or sex != "F" or not 18 <= age <= 49 or pregnancy == "pregnant":
            return {**common, "status": "outside_scope", "reasonCodes": ["outside_evaluated_population"],
                    "message": "Прогноз ферритина проверен для небеременных женщин 18–49 лет; этот ввод вне проверенной области."}
        if age <= 44 and pregnancy == "unknown":
            return {**common, "status": "unknown_pregnancy", "reasonCodes": ["pregnancy_status_unknown"],
                    "message": "Для прогноза ферритина до 45 лет укажите статус беременности. Неизвестный статус не считается отсутствием беременности."}
        from ml_baselines.biochemical import feature_names, predictor_frame
        from ml_baselines.core import CBC, LABS, encode
        import pandas as pd
        frame = encode(pd.DataFrame([inputs]))
        extras = [name for name in feature_names("low_ferritin", "extended") if name not in CBC]
        route = "extended" if frame[extras].notna().any(axis=1).iloc[0] else "cbc"
        x = predictor_frame(frame, "low_ferritin", route)
        count = int(x[[name for name in x if name in LABS]].notna().sum(axis=1).iloc[0])
        if count < 5:
            return {**common, "status": "insufficient_data", "availableLabCount": count,
                    "reasonCodes": ["insufficient_supported_labs"],
                    "message": "Прогноз ферритина не выполнен: нужны минимум пять поддержанных показателей ОАК/биохимии."}
        with self.lock:
            score = float(self.bundle["heads"][("low_ferritin", route)].predict_score(frame)[0])
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Invalid ferritin score")
        threshold = float(self.bundle["thresholds"][("low_ferritin", route)])
        return {**common, "status": "research_prediction", "route": route, "score": score,
                "scoreMeaning": "research_score_for_low_biochemical_marker", "decisionThreshold": threshold,
                "screenPositive": score >= threshold, "referenceCutoff": 15., "availableLabCount": count,
                "externalGatePassed": True,
                "message": "Оценка выше исследовательского порога для низкого ферритина." if score >= threshold else
                           "Оценка ниже исследовательского порога. Это не исключает низкий ферритин или дефицит железа.",
                "limitation": LIMITATION}

    def apply(self, report, pregnancy="unknown"):
        try:
            result = self.evaluate(report["inputs"], pregnancy)
        except Exception:
            result = {"endpoint": "low_ferritin", "status": "unavailable", "label": "Прогноз низкого ферритина",
                      "modelFamily": "biochemical_v4", "modelVersion": self.version, "researchOnly": True,
                      "clinicalValidated": False, "reasonCodes": ["inference_failed"],
                      "message": "Прогноз ферритина недоступен: ошибка расчёта. Прогноз не заменён нормальным результатом."}
        report["ferritinScreening"] = result
        report["pregnancyStatus"] = pregnancy
        report["modelConfiguration"] = (report.get("modelFamily", "hemoglobin_rule") + "_plus_ferritin_v4"
                                        if self.bundle is not None else report.get("modelFamily", "hemoglobin_rule"))
        if result["status"] == "unavailable":
            report["warnings"].append(result["message"])
        return report
