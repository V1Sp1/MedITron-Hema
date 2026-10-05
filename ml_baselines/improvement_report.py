"""Compare frozen candidates, including actual API abstention and reused-test caveats."""

import json
from pathlib import Path
from threading import Lock

import numpy as np
import pandas as pd
from sklearn.metrics import f1_score

from .core import CBC, DEFICIENCIES, FEATURES, LABS, TARGETS

RELEASE_POLICY = {"set_before_reading_v2_holdout": True, "cv_mean_primary_gain_at_least": .01,
                  "minimum_all_panel_mean_deficit_holdout_delta": -.03,
                  "minimum_primary_class_holdout_delta": -.03,
                  "minimum_available_api_accuracy_delta": -.03,
                  "minimum_available_api_coverage_delta": -.01,
                  "note": "Repeated holdout can block an unsafe promotion, but does not select or retune candidates. Its prior use limits independence."}


def release_checks(comparison, deployment, policy):
    checks = []
    def check(code, delta, minimum):
        checks.append({"check": code, "delta": float(delta), "minimum": float(minimum), "passed": bool(delta >= minimum)})
    dev = comparison[comparison.partition.eq("development_oof") & comparison["mode"].eq("primary")]
    check("development_primary_gain", dev.delta_f1.mean(), policy["cv_mean_primary_gain_at_least"])
    test = comparison[comparison.partition.eq("reused_holdout")]
    for view in ("available", "drop30", "drop60", "cbc_only"):
        check("mean_deficit_" + view, test[test.target.isin(DEFICIENCIES) & test.scenario.eq(view)].delta_f1.mean(), policy["minimum_all_panel_mean_deficit_holdout_delta"])
    for view in ("available", "drop30", "drop60"):
        check("class_" + view, test[test.target.eq("anemia_class") & test.scenario.eq(view)].delta_f1.iloc[0], policy["minimum_primary_class_holdout_delta"])
    api = deployment[deployment.partition.eq("reused_holdout") & deployment.scenario.eq("available")].set_index("version")
    check("available_api_accuracy", api.loc["robust_v2"].accepted_class_accuracy-api.loc["baseline_v1"].accepted_class_accuracy, policy["minimum_available_api_accuracy_delta"])
    check("available_api_coverage", api.loc["robust_v2"].coverage-api.loc["baseline_v1"].coverage, policy["minimum_available_api_coverage_delta"])
    return {"promote_default": all(c["passed"] for c in checks), "checks": checks,
            "candidate_selection_changed": False, "independent_validation": False,
            "reason": "The frozen candidate must pass every release check; failures preserve the active baseline."}


def table(frame):
    def cell(value):
        if isinstance(value, (float, np.floating)):
            return "—" if np.isnan(value) else f"{value:.3f}"
        return str(value)
    return "| " + " | ".join(frame.columns) + " |\n| " + " | ".join(["---"] * len(frame.columns)) + " |\n" + "\n".join("| " + " | ".join(cell(v) for v in row) + " |" for row in frame.itertuples(index=False, name=None))


def replay(data, frame, indices, scores, selected, labels, name, partition, view):
    from backend.model_service import ModelService
    from backend.screening import screen
    service = object.__new__(ModelService)
    service.bundle, service.version, service.lock = {}, "oof-replay", Lock()
    n_eligible = n_evaluated = correct = inconsistent = sparse = 0
    states = {}
    predictions, truths = [], []
    for j, idx in enumerate(indices):
        inputs = {k: None if pd.isna(frame.loc[idx, k]) else float(frame.loc[idx, k]) for k in FEATURES if k != "sex"}
        sex = frame.loc[idx, "sex"]
        inputs["sex"] = None if pd.isna(sex) else "F" if sex == 0 else "M"
        if any(inputs[k] is None for k in ("age_years", "sex", "hemoglobin")):
            states["input_rejected"] = states.get("input_rejected", 0) + 1
            continue
        available = [k for k in LABS if inputs[k] is not None]
        panel = "cbc" if all(k in CBC for k in available) else "primary"
        raw = {"panel": panel, "warnings": [], "scores": {}}
        for target in TARGETS:
            config = selected[target][panel]
            p = scores[f"{config}__{target}__{view}"][j]
            raw["scores"][target] = {"model": config, "values": {str(label): float(value) for label, value in zip(labels[target], p)}}
        service.infer = lambda bundle, values: raw
        result = service.apply(screen(inputs, "doctor"))
        state = result["modelDecisionState"]
        states[state] = states.get(state, 0) + 1
        n_eligible += 1
        inconsistent += state == "inconsistent"
        sparse += state == "suppressed_sparse"
        predictions.append(max(raw["scores"]["anemia_class"]["values"], key=raw["scores"]["anemia_class"]["values"].get))
        truths.append(data.loc[idx, "anemia_class"])
        if state == "evaluated":
            n_evaluated += 1
            correct += result["prediction"]["code"] == data.loc[idx, "anemia_class"]
    return {"version": name, "partition": partition, "scenario": view, "n": len(indices), "eligible_n": n_eligible,
            "evaluated_n": n_evaluated, "coverage": n_evaluated / n_eligible if n_eligible else 0,
            "accepted_class_accuracy": correct / n_evaluated if n_evaluated else np.nan,
            "raw_class_macro_f1": f1_score(truths, predictions, labels=labels["anemia_class"], average="macro", zero_division=0) if truths else np.nan,
            "inconsistent_n": inconsistent, "sparse_n": sparse, "states": json.dumps(states, sort_keys=True)}


def write_report(output, baseline, data, views, dev, holdout):
    old_selection = json.loads((baseline / "selection.json").read_text())
    selected = json.loads((output / "selection.json").read_text())
    labels = json.loads((baseline / "manifest.json").read_text())["labels"]
    old_metrics = pd.read_csv(baseline / "metrics.csv")
    new_metrics = pd.read_csv(output / "metrics.csv")
    comparison = []
    for partition, new_part in (("development_oof", "development_oof"), ("holdout", "reused_holdout")):
        for target in TARGETS:
            for mode, scenarios in (("primary", ["available", "drop30", "drop60"]), ("cbc", ["cbc_only"])):
                for view in scenarios:
                    a = old_metrics[old_metrics.partition.eq(partition) & old_metrics.target.eq(target) & old_metrics.scenario.eq(view) & old_metrics.config.eq(old_selection[target][mode])].iloc[0]
                    b = new_metrics[new_metrics.partition.eq(new_part) & new_metrics.target.eq(target) & new_metrics.scenario.eq(view) & new_metrics.config.eq(selected[target][mode])].iloc[0]
                    comparison.append({"partition": new_part, "target": target, "mode": mode, "scenario": view,
                                       "v1_f1": a.f1, "v2_f1": b.f1, "delta_f1": b.f1-a.f1,
                                       "v1_precision": a.get("precision"), "v2_precision": b.get("precision"),
                                       "v1_recall": a.get("recall"), "v2_recall": b.get("recall"),
                                       "v1_brier": a.get("brier"), "v2_brier": b.get("brier")})
    comp = pd.DataFrame(comparison)
    comp.to_csv(output / "comparison.csv", index=False)
    replays = []
    for partition, indices, file in (("development_oof", dev, "oof_probabilities.npz"), ("reused_holdout", holdout, "holdout_probabilities.npz")):
        for name, directory, selection in (("baseline_v1", baseline, old_selection), ("robust_v2", output, selected)):
            with np.load(directory / file) as scores:
                for view in ("available", "drop30", "drop60", "cbc_only", "hb_only"):
                    replays.append(replay(data, views[view], indices, scores, selection, labels, name, partition, view))
    deployment = pd.DataFrame(replays)
    deployment.to_csv(output / "api_policy_metrics.csv", index=False)
    policy = json.loads((output / "release_policy.json").read_text())
    decision = release_checks(comp, deployment, policy)
    (output / "release_decision.json").write_text(json.dumps(decision, indent=2), encoding="utf-8")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figures, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    for ax, part in zip(axes, ("development_oof", "reused_holdout")):
        subset = comp[comp.partition.eq(part) & comp["mode"].eq("primary") & comp.scenario.eq("drop60")]
        positions = np.arange(len(subset))
        ax.bar(positions-.18, subset.v1_f1, width=.36, label="v1")
        ax.bar(positions+.18, subset.v2_f1, width=.36, label="v2")
        ax.set_xticks(positions, [t.replace("_deficiency", "").replace("_anemia", "") for t in subset.target], rotation=35, ha="right")
        ax.set_ylim(0, 1.05);ax.set_title(part);ax.set_ylabel("F1 (class: macro)");ax.legend()
    figures.tight_layout();figures.savefig(output / "missingness_comparison.png", dpi=160);plt.close(figures)
    columns = ["target", "mode", "scenario", "v1_f1", "v2_f1", "delta_f1"]
    summary = comp[comp.partition.eq("development_oof")].groupby(["target", "mode"])[["v1_f1", "v2_f1", "delta_f1"]].mean().reset_index()
    shortlist = [{"target": t, "primary": selected[t]["primary"], "cbc": selected[t]["cbc"]} for t in TARGETS]
    text = """# Вторая серия моделей Hema: robust_v2

Выбор зафиксирован по development до повторной проверки holdout. Исходные данные и v1 не изменены.

## Метод и границы выводов

- Те же 672 development-пациента, 168 holdout и пять разбиений из baseline_v1. На holdout модели, preprocessing, веса классов и пороги не обучались.
- Шесть новых конфигураций: CatBoost/Extra Trees с пятью представлениями обучающих пациентов, умеренными весами классов sqrt(inverse frequency), вычисляемыми признаками и узкими панелями. Медианы/маски fit только на train. Возраст, пол, Hb-margin, логарифмы и отношения вычисляются только из входных значений; разметка не входит в X. Пропуски не превращаются в нули.
- Дополнительный кандидат для 12 классов: обнуление несовместимых с известным статусом Hb классов и нормализация. B6/медь остаются допустимыми при обоих статусах, неизвестные Hb/пол не ограничивают классы. Это согласование со смыслом кейсовой разметки, не новая клиническая вероятность и не подтверждение отсутствия дефицитов. Смешанные дефициты без анемии сохраняются как бинарные гипотезы; отдельного класса для них в CSV нет.
- Сравниваются новые и старые кандидаты. Primary: средний F1 available/drop30/drop60, CBC: F1 cbc_only. Замена требует +0,01 CV F1, падение F1 исходной панели не более 0,02, precision не более 0,10 и specificity не более 0,02 для бинарных целей. Иначе сохраняется v1. Все пороги решений 0,5; калибровка не выполнялась.
- Выбор и отчёт на одних OOF означают оптимизм оценки победителя. Holdout уже был открыт в v1; его повторное использование **не является новой независимой валидацией**. Для итогового качества нужна новая реальная когорта. Внешние наборы с неподтверждёнными метками в обучение не добавлены.
- Всего 28 положительных B6/меди в development и 7 на holdout. Изменение нескольких ошибок сильно двигает F1. Вес классов может улучшать полноту при ухудшении Brier; все scores сохраняют calibrated=false.

## Выбранные кандидаты

""" + table(pd.DataFrame(shortlist)) + "\n\n## Development: критерий выбора\n\n" + table(summary) + "\n\n## Повторный holdout: исходная панель и ОАК\n\n" + table(comp[comp.partition.eq("reused_holdout") & comp.scenario.isin(["available", "cbc_only"])][columns])
    text += "\n\n## Повторный holdout: удалено 60% измеренных анализов\n\n" + table(comp[comp.partition.eq("reused_holdout") & comp.scenario.eq("drop60")][columns])
    text += "\n\n![Устойчивость к пропускам](missingness_comparison.png)\n\n## Реальный слой отказа API\n\nПо каждому OOF/test-ответу воспроизведены текущие правила ModelService и полноты данных. eligible_n — ввод с обязательными возрастом/полом/Hb; coverage — доля evaluated среди них. accepted_class_accuracy — совпадение с CSV только среди выданных заключений, не клиническая точность. Это отдельные метрики от F1 всех исследовательских предсказаний. Увеличение coverage само по себе не является улучшением.\n\n"
    text += table(deployment[["version", "partition", "scenario", "eligible_n", "evaluated_n", "coverage", "accepted_class_accuracy", "inconsistent_n"]])
    text += "\n\n## Решение о рабочей версии\n\n" + ("Кандидат прошёл все установленные проверки перехода. Это не клиническая валидация." if decision["promote_default"] else "Кандидат не прошёл все проверки перехода: рабочая baseline_v1 сохранена. Набор v2 не пересобирается по результатам holdout; он остаётся отдельным исследовательским кандидатом.")
    text += "\n\n" + table(pd.DataFrame(decision["checks"])[["check", "delta", "minimum", "passed"]])
    text += "\n\nДля отдельного локального сравнения: `.venv/bin/python -m backend --port 8001 --data-dir data/local_v2 --model-bundle experiments/robust_v2/models/selected.joblib`. Основной сервер на 8000 сохраняет v1. `data/local_v2` — отдельная база, чтобы не смешивать результаты разных экземпляров.\n"
    text += "\n\n## Артефакты\n\nmanifest.json фиксирует кандидаты и ограничения до обучения, selection.json — решения до повторной проверки. comparison.csv, metrics.csv, per_class.csv, api_policy_metrics.csv, OOF/test NPZ и selected.joblib содержат воспроизводимые результаты. v1 остаётся доступной для отката. Запуск: `.venv/bin/python -m ml_baselines.improve --output experiments/robust_v2`. Существующая папка не перезаписывается.\n"
    (output / "REPORT.md").write_text(text, encoding="utf-8")
