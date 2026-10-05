"""Explicit model decision codes; probabilities and free-text labels are not decisions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Deficit = Literal["iron", "B12", "folate", "B6", "copper"]
AnemiaClass = Literal["no_anemia_no_deficiency", "latent_deficiency", "iron_deficiency_anemia",
                      "B12_deficiency_anemia", "B12_deficiency_no_anemia", "folate_deficiency_anemia",
                      "folate_deficiency_no_anemia", "B6_deficiency", "copper_deficiency",
                      "inflammation_anemia", "mixed_deficiency", "anemia_other"]


class ModelVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    anemiaClass: AnemiaClass | None = None
    deficits: list[Deficit] | None = Field(default=None, max_length=5)
    inflammation: bool | None = None
    status: Literal["evaluated", "uncertain", "out_of_scope"] = "evaluated"

    @model_validator(mode="after")
    def distinct_deficits(self):
        if self.deficits is not None and len(self.deficits) != len(set(self.deficits)):
            raise ValueError("Дефициты в вердикте не должны повторяться.")
        return self


CLASS_DEFICITS = {"iron_deficiency_anemia": {"iron"}, "B12_deficiency_anemia": {"B12"},
                  "B12_deficiency_no_anemia": {"B12"}, "folate_deficiency_anemia": {"folate"},
                  "folate_deficiency_no_anemia": {"folate"}, "B6_deficiency": {"B6"},
                  "copper_deficiency": {"copper"}}
ANEMIA_CLASSES = {"iron_deficiency_anemia", "B12_deficiency_anemia", "folate_deficiency_anemia",
                  "inflammation_anemia", "mixed_deficiency", "anemia_other"}
NON_ANEMIA_CLASSES = {"no_anemia_no_deficiency", "latent_deficiency", "B12_deficiency_no_anemia",
                      "folate_deficiency_no_anemia"}


def verdict_context(report: dict, data: dict, *, simulation: bool = False) -> dict:
    base = {"modelState": "not_connected", "anemiaClass": None, "deficits": [], "inflammation": False,
            "reasonCodes": []}
    if not simulation and report.get("modelConnected") is not True:
        return base
    raw = report.get("modelVerdict")
    if raw is None:
        return {**base, "modelState": "missing_verdict" if report.get("modelConnected") else "not_connected"}
    verdict = ModelVerdict.model_validate(raw)
    kind = verdict.anemiaClass
    stated = None if verdict.deficits is None else set(verdict.deficits)
    inferred = CLASS_DEFICITS.get(kind, set())
    deficits = inferred if stated is None else stated
    reasons = []
    if bool(inferred) and inferred != deficits:
        reasons.append("class_deficits_mismatch")
    if kind in {"no_anemia_no_deficiency", "anemia_other"} and bool(deficits):
        reasons.append("class_deficits_mismatch")
    if kind == "latent_deficiency" and stated is not None and not deficits:
        reasons.append("latent_without_deficit")
    if kind == "mixed_deficiency" and stated is not None and len(deficits) < 2:
        reasons.append("mixed_without_components")
    if ((kind in ANEMIA_CLASSES and report.get("anemia") is not True)
            or (kind in NON_ANEMIA_CLASSES and report.get("anemia") is not False)):
        reasons.append("class_hb_mismatch")
    # This flag is the model target inflammation_anemia, not general inflammation.
    if verdict.inflammation is True and report.get("anemia") is False:
        reasons.append("inflammation_without_anemia")
    if ((kind == "inflammation_anemia" and verdict.inflammation is False)
            or (kind in {"no_anemia_no_deficiency", "anemia_other"} and verdict.inflammation is True)):
        reasons.append("class_inflammation_mismatch")
    if reasons:
        state = "inconsistent"
    elif verdict.status != "evaluated":
        state = verdict.status
    elif data["level"] == "insufficient":
        state = "suppressed_sparse"
    elif kind is None and stated is None and verdict.inflammation is None:
        state = "missing_verdict"
    elif not simulation and not report.get("modelVersion"):
        state = "missing_verdict"
    else:
        state = "evaluated"
    if state != "evaluated":
        return {**base, "modelState": state, "reasonCodes": list(dict.fromkeys(reasons))}
    return {"modelState": state, "anemiaClass": kind, "deficits": sorted(deficits),
            "inflammation": verdict.inflammation is True or kind == "inflammation_anemia", "reasonCodes": []}
