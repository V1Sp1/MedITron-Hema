"""Reproducible comparison of saved nested predictions, transfer trials and API policy."""

import hashlib
import json
from pathlib import Path
import shutil

import joblib
import numpy as np
import pandas as pd

from .calibration import probability_metrics, reliability_bins
from .core import ROOT, SCENARIOS, TARGETS, encode, scenario
from .improve import dump
from .improvement_report import replay, table


def write_report(output):
    manifest = json.loads((output / "manifest.json").read_text())
    bundle = joblib.load(output / "models/selected.joblib")
    choices = json.loads((output / "calibration_selection.json").read_text())
    data = pd.read_csv(ROOT / "data/case/deficiency_anemia.csv")
    splits = pd.read_csv(output / "splits.csv")
    dev = splits.loc[splits.partition.eq("development"), "row_index"].to_numpy()
    held = splits.loc[splits.partition.eq("holdout"), "row_index"].to_numpy()
    x = encode(data)
    views = {v: scenario(x, v, manifest["seed"]+100+i) for i,v in enumerate(SCENARIOS)}
    comparison, decisions, fold_metrics = [], [], []
    for filename, partition, indices in [("oof_probabilities.npz","nested_development_oof",dev), ("holdout_probabilities.npz","reused_holdout",held)]:
        with np.load(output / filename) as stored:
            caches = {"baseline_v1": {}, "calibrated_v3": {}}
            for target in TARGETS:
                for route in ("primary","cbc"):
                    method = choices[target][route]["method"] if partition == "nested_development_oof" else "selected"
                    for view in SCENARIOS:
                        a, b = (stored[f"{route}__{target}__{m}__{view}"] for m in ["identity",method])
                        name = bundle["selected"][target][route]
                        caches["baseline_v1"][f"{name}__{target}__{view}"] = a
                        caches["calibrated_v3"][f"{name}__{target}__{view}"] = b
                        y = data.iloc[indices][target].to_numpy()
                        ma, mb = (probability_metrics(y, scores, bundle["labels"][target]) for scores in [a,b])
                        comparison.append({"partition":partition,"target":target,"route":route,"scenario":view,"method":choices[target][route]["method"],
                                           **{f"raw_{k}":v for k,v in ma.items()}, **{f"selected_{k}":v for k,v in mb.items()},
                                           **{f"delta_{k}":mb[k]-ma[k] for k in ["log_loss","brier","ece_10","f1"]}})
                        if partition == "nested_development_oof":
                            for fold in range(manifest["outer_folds"]):
                                mask = splits.loc[dev,"fold"].to_numpy() == fold
                                for version, scores in [("raw",a),("selected",b)]:
                                    fold_metrics.append({"fold":fold,"target":target,"route":route,"scenario":view,"version":version,
                                                         **probability_metrics(y[mask], scores[mask], bundle["labels"][target])})
            for view in ["available","drop30","drop60","cbc_only","hb_only","no_iron","no_B12","no_folate","no_B6","no_copper"]:
                for version in caches:
                    decisions.append(replay(data, views[view], indices, caches[version], bundle["selected"], bundle["labels"], version, partition, view))
    comp = pd.DataFrame(comparison)
    comp.to_csv(output / "comparison.csv", index=False)
    pd.DataFrame(fold_metrics).to_csv(output / "fold_metrics.csv", index=False)
    deployment = pd.DataFrame(decisions)
    deployment.to_csv(output / "deployment_metrics.csv", index=False)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plots = [("anemia_class","primary","available"),("anemia_class","primary","drop60"),("iron_deficiency","primary","drop60"),
             ("B12_deficiency","primary","drop60"),("folate_deficiency","cbc","cbc_only"),("copper_deficiency","cbc","cbc_only")]
    fig, axes = plt.subplots(2,3,figsize=(13,8))
    with np.load(output / "oof_probabilities.npz") as stored:
        for ax,(target,route,view) in zip(axes.flat,plots):
            for method, label in [("identity","Raw"),(choices[target][route]["method"],"Selected")]:
                bins = reliability_bins(data.iloc[dev][target].to_numpy(), stored[f"{route}__{target}__{method}__{view}"], bundle["labels"][target])
                ax.plot([b["mean_score"] for b in bins],[b["observed"] for b in bins],"o-",label=label,alpha=.8)
            ax.plot([0,1],[0,1],"--",color="grey")
            ax.set(xlim=(-.02,1.02),ylim=(-.02,1.02),title=f"{target}\n{route}/{view}",xlabel="Mean predicted score",ylabel="Observed fraction")
            ax.legend();ax.grid(alpha=.2)
    fig.suptitle("Development: nested calibration assessment (10 bins; see bin counts in CSV)")
    fig.tight_layout();fig.savefig(output / "reliability.png",dpi=150);plt.close(fig)
    selected_table = pd.DataFrame([{"target":t, **{mode:choices[t][mode]["method"] for mode in ("primary","cbc")}} for t in TARGETS])
    cols = ["target","route","scenario","raw_log_loss","selected_log_loss","raw_brier","selected_brier","raw_ece_10","selected_ece_10","raw_f1","selected_f1"]
    natural = comp[comp.route.eq("primary") & comp.scenario.eq("available")]
    deployment_cols = ["version","partition","scenario","eligible_n","evaluated_n","coverage","accepted_class_accuracy"]
    transfer = pd.read_csv(output / "external_metrics.csv")
    summary = transfer[transfer.partition.eq("development_oof") & transfer.scenario.isin(["available","drop30","drop60"])].groupby(["target","method"])[["log_loss","brier","f1"]].mean().reset_index()
    report = """# Калибровка Hema и перенос внешних меток: calibrated_v3

Обучены калибраторы текущего baseline_v1. Внешние данные проверены отдельным опытом, в калибровку они не входят.
По development выбраны 10 преобразований из 14: 6 primary и 4 ОАК. Остальные оценки сохранены исходными.
Поэтому статус набора **partial**, `calibrationApplied=true`, общий `calibrated=false`; применённый метод виден для каждой задачи/панели.
Это калибровка по частично синтетическим меткам кейса, не подтверждение клинических вероятностей.

## Протокол

- Замороженные 672 development / 168 holdout и пять внешних фолдов baseline_v1. Базовые алгоритмы/гиперпараметры не менялись.
- Внутри train каждого внешнего фолда ещё три фолда по пациентам, стратифицированные по anemia_class. На их OOF обучается калибратор; оценка — только на внешнем validation. Все копии пациента после удаления анализов остаются в его train. Нет обучения калибратора на предсказаниях для пациентов, использованных соответствующей базовой моделью. `split_audit.json` хранит все 210 внутренних разбиений.
- Primary: available/drop30/drop60, равный общий вес каждого пациента, по 1/3 для представления. ОАК: cbc_only. Механизм удаления анализов фиксирован; калибровка не переносится автоматически на любой клинический механизм пропусков.
- Сравнение identity / temperature, для бинарных задач также монотонный sigmoid от logit(score). Слабая регуляризация к identity, slope>0. Temperature сохраняет argmax и бинарный порог 0,5. Isotonic не использован при малом числе положительных.
- Выбор: снижение среднего log loss ≥2%, рост Brier ≤0,001, ECE ≤0,01, падение F1 в любом сценарии отбора ≤0,02. Порог решений 0,5 не подбирался. Критерии записаны до обучения в manifest.json; calibration_selection.json — до holdout.
- Финальные калибраторы fit на пятифолдовых OOF 672 development-пациентов, затем применяются к прежним базовым весам на всех 672. Это схема с одним финальным базовым estimator; распределение scores при меньшем inner-train может отличаться от финального.
- Известный нормальный Hb по-прежнему обнуляет воспалительную анемию после преобразования. Новых диагностических правил или Hb-conditioning 12 классов не добавлено.
- Внешняя оценка калибратора отделена от его fit, но исходный выбор базовых алгоритмов v1 и выбор метода уже использовали development. Оценка выбранного метода по тем же фолдам содержит оптимизм. Holdout открыт ранее и здесь повторный; он не независимая новая когорта. Значения не являются обещанием качества на реальных пациентах.

## Выбранные методы

""" + table(selected_table)
    report += "\n\n## Исходная панель: nested development\n\n" + table(natural[natural.partition.eq("nested_development_oof")][cols])
    report += "\n\n## Исходная панель: повторный holdout\n\n" + table(natural[natural.partition.eq("reused_holdout")][cols])
    report += "\n\n## Пропуски и ОАК: повторный holdout\n\n" + table(comp[comp.partition.eq("reused_holdout") & ((comp.route.eq("primary") & comp.scenario.eq("drop60")) | (comp.route.eq("cbc") & comp.scenario.eq("cbc_only")))][cols])
    report += """\n\n![Кривые надежности](reliability.png)

Log loss и Brier измеряют качество вероятностных предсказаний, объединяя различение и калибровку; снижение одного показателя не доказывает калибровку само по себе. ECE здесь 10 равных интервалов: для бинарных целей положительная вероятность, для multiclass максимальная оценка против правильности argmax. `reliability_bins.csv` содержит число записей в каждом интервале; ECE зависит от разбиения и особенно нестабилен для редких целей. Brier: бинарный стандартный, multiclass сумма ошибок по 12 классам — между ними значения не сравниваются.

На holdout B6/медь всего по 7 положительных; на development по 28. Даже улучшение log loss не устраняет отсутствие анализов и не подтверждает выявление редких дефицитов по ОАК. F1 меди по ОАК на повторном holdout остаётся 0.

## Что происходит с решениями API

Сохранённые OOF/test scores воспроизведены через текущие политики `ModelService` и полноты ввода с реальной маршрутизацией. При отсутствии возраста/пола/Hb запрос исключён из eligible_n; ОАК определяется по введённым лабораторным признакам. `coverage` — доля выданных причинных выводов среди допустимых вводов, `accepted_class_accuracy` — совпадение с CSV только среди них. Это не клиническая точность и не полнота выявления. Более резкие оценки способны увеличить coverage при прежнем пороге 0,5, поэтому этот слой оценивается отдельно.

""" + table(deployment[deployment.scenario.isin(["available","drop60","cbc_only"])][deployment_cols])
    report += """\n\n## Внешнее дообучение: Kılıçarslan

Источник: Serhat Kılıçarslan, Mete Celik, Safak Şahin, Mendeley DOI 10.17632/dt89jydgnv.1, CC BY 4.0. Использована подготовленная копия `case_units_verified_source_partial.csv`: 10 документированных признаков, Hb/MCHC ×10, serum iron ×0,1791; неподтверждённые B12/RBC/WBC/пол/TIBC/TSAT не импортированы. Возраст источника неизвестен, метод/популяция отличаются от кейса. Единицы сами по себе не устанавливают эквивалентность методов.

Только положительные исходные метки классов 2/4/3 → iron/B12/folate. Нули one-hot не перенесены. Неизвестные цели исключены из loss соответствующей задачи. Исходные классы приоритетны и скрывают сочетания; эти слабые положительные метки не равны подтверждённым независимым диагнозам. Multiclass, B6/медь/воспаление не обучались на источнике.

Сохранённые QC-флаги временно исключены из опыта, группы повторных полных векторов дедуплицированы; оригиналы не изменены. По задачам использованы 4152 / 195 / 150 положительных записей. Нет внешних отрицательных примеров; это может смещать априорную вероятность. Суммарный вес внешних данных =10% case-train, без ручного подбора по holdout. У аугментированных копий сохраняется отношение весов. Для всех пяти фолдов те же case-train/validation и базовый алгоритм; источник никогда не используется для калибратора.

Критерий замены: средний F1 трёх сценариев ≥+0,01, падение в любом сценарии ≤0,02, рост Brier ≤0,001. Все три задачи **не прошли** его. На повторном holdout фолаты улучшились, но этот результат не переопределяет прежний development-выбор. Внешние веса сохранены только для дальнейших опытов (`models/external_partial.joblib`), API их не принимает как поддержанное семейство. Нет независимой оценки качества на внешних диагнозах или циклах.

""" + table(summary)
    report += """\n\n## NHANES: подготовленные единицы и маски

`scripts/prepare_nhanes.py` создал `data/external/processed/nhanes_case_units_v1/`: 5351 взрослый 2003–2004 (25 признаков) и 5807 взрослых 2011–2012 (18), исключена известная беременность, неизвестная отмечена. ID = цикл + SEQN, соединения один-к-одному. Hb/MCHC г/дл→г/л ×10; CRP 2003 мг/дл→мг/л ×10; MMA 2011 нмоль/л→мкмоль/л ×0,001. Пропуски сохраняются, общий билирубин не становится непрямым. Сохранены веса/страты/PSU, коды LOD, ограничения методов и ограниченный сверху возраст. Подсчёты невзвешенные.

Все десять целей кейса неизвестны и имеют loss-mask 0. Отдельно рассчитан только `anemia_by_case_rule`, без утверждения причины. Биомаркеры не превращены в подтверждённые дефициты или отрицательные диагнозы. B6 2003 не допускается к автоматическому объединению из-за документированного сдвига метода. NHANES готов для анализа распределений и разработки отдельных биомаркерных задач; клиническую калибровку нашего multiclass выполнить по этим файлам нельзя.

## Подключение и следующий этап

Набор `models/selected.joblib` содержит прежние базовые модели и 14 сохранённых калибраторов (4 identity). API умеет применять их в обеих панелях и возвращает методы/границы калибровки. `calibrationApplied=true` не означает клиническую валидацию; `calibrated=false` честно отражает сохранённые сырые головы. По заранее записанной политике promotion требует новой независимой размеченной выборки, поэтому default baseline_v1 сохранён; v3 доступен как отдельный исследовательский кандидат. Отказы при малом вводе и правила рекомендаций сохраняются.

Для следующей итерации нужны подтверждение единиц/методов B12 источника, независимые положительные и отрицательные диагнозы с масками неизвестности, метки B6/меди и новая когорта проверки. Затем сравнить отдельное биомаркерное предобучение/источник с учётом доменного сдвига, калибровку на реальной целевой популяции и качество среди новых выданных выводов.

Воспроизведение: `.venv/bin/python -m ml_baselines.calibrate --output experiments/NEW_NAME`, затем `.venv/bin/python -m ml_baselines.calibration_report --output experiments/NEW_NAME`. Нормализация NHANES: `.venv/bin/python scripts/prepare_nhanes.py` (не перезаписывает предыдущие производные). Весовой bundle joblib загружается только из доверенного локального источника.

Методическая документация: [scikit-learn probability calibration](https://scikit-learn.org/stable/modules/calibration.html). Документация CDC и атрибуция исходников находятся в unit_mapping.json, manifest.json и EXTERNAL_DATASETS.md.
"""
    (output / "REPORT.md").write_text(report, encoding="utf-8")
    runtime = output / "runtime_source_snapshot"
    runtime.mkdir(exist_ok=True)
    runtime_files = ["ml_baselines/calibration.py","ml_baselines/predict.py","ml_baselines/calibration_report.py","backend/model_service.py","backend/screening.py","backend/data_sufficiency.py","backend/verdicts.py","backend/app.py","backend/data/model_trust.json","scripts/prepare_nhanes.py","tests/test_calibration.py"]
    for filename in runtime_files:
        destination = runtime / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / filename, destination)
    dump(output / "runtime_manifest.json", {"runtime_code_sha256": {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in runtime_files},
                                          "note": "Training code is frozen separately in source_snapshot; runtime adds saved-calibrator support and current API policy replay."})
    dump(output / "artifact_hashes.json", {str(p.relative_to(output)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.rglob("*")) if p.is_file() and p.name != "artifact_hashes.json"})
    print(deployment[deployment.scenario.eq("available")][deployment_cols].to_string(index=False))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "experiments/calibrated_v3")
    write_report(parser.parse_args().output.resolve())
