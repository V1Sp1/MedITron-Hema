"""Case-specified Hb rule; model_service augments this report when enabled."""

from .features import validate_inputs
from .data_sufficiency import assess_data


def screen(inputs: dict, audience: str) -> dict:
    inputs = validate_inputs(inputs)
    threshold = 120 if inputs["sex"] == "F" else 130
    return {"audience": audience, "inputs": inputs, "hemoglobin": inputs["hemoglobin"],
            "threshold": threshold, "anemia": inputs["hemoglobin"] < threshold,
            "prediction": None, "modelVerdict": None, "deficiencies": None, "deficiencyProbabilities": [],
            "anemiaProbabilities": [], "modelConnected": False, "modelVersion": None,
            "source": "api-rule", "method": "Правило порога Hb из кейса СУ; модель дефицитов не подключена",
            "missing": [key for key, value in inputs.items() if value is None],
            "dataSufficiency": assess_data(inputs, audience),
            "warnings": ["Результат рассчитан локальным сервером по правилу Hb из кейса. Модель дефицитов и причин анемии пока не подключена."]}
