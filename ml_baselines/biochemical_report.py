"""Report real-data v4 results without refitting or changing locked thresholds."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .train_biochemical import OUTPUT
from .external_validate import sha,write_json

NAMES = {'low_ferritin':'Низкий ферритин','low_B12':'Низкий B12','low_PLP':'Низкий PLP (B6)'}


def comparison(metrics,view):
    lines = ['| Цель | Модель | n / низкий маркер | F1 | Sensitivity | Specificity | Precision | AP | AUROC |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for endpoint,name in NAMES.items():
        for model in ['v1_default','v1_tuned','v4']:
            r = metrics.loc[(metrics.endpoint == endpoint)&(metrics.view == view)&(metrics.model == model)].iloc[0]
            ppv = 'не определён' if pd.isna(r.precision) else f'{r.precision:.1%}'
            lines.append(f'| {name} | {model} | {r.n} / {r.positives} | {r.F1:.3f} | {r.sensitivity:.1%} | {r.specificity:.1%} | {ppv} | {r.AP:.3f} | {r.AUROC:.3f} |')
    return '\n'.join(lines)


def run(output):
    protocol = json.loads((output/'protocol.json').read_text())
    selection = json.loads((output/'selection.json').read_text())
    lock = json.loads((output/'selection_lock.json').read_text())
    gates = json.loads((output/'external_gates.json').read_text())
    m = pd.read_csv(output/'external_metrics.csv')
    dev = pd.read_csv(output/'development_metrics.csv')
    manifest = json.loads((output/'test_cohort_manifest.json').read_text())
    selected_rows = ['| Цель / панель | Выбранная модель | Development n / positives | Порог v4 | Порог v1 tuned |',
                     '|---|---|---:|---:|---:|']
    for key,v in selection.items():
        selected_rows.append(f'| {key} | {v["selected"]} | {v["n"]} / {v["positives"]} | {v["threshold"]:.5f} | {v["legacy_threshold"]:.5f} |')
    selected_table = '\n'.join(selected_rows)
    gate_rows = ['| Голова | Критерий улучшения | Парный 95% интервал прироста sensitivity относительно v1 tuned |',
                 '|---|---|---:|']
    for key,g in gates.items():
        ci = g['test']['paired_sensitivity_gain_95ci']
        failed = ', '.join(k for k,v in g['checks'].items() if not v)
        gate_rows.append(f'| {key} | {"Пройден" if g["passed"] else "Не пройден: "+failed} | {ci[0]:+.1%} … {ci[1]:+.1%} |')
    gate_table = '\n'.join(gate_rows)
    fig,axes = plt.subplots(1,3,figsize=(13,4.5),layout='constrained')
    colors = ['#aeb8c2','#637f98','#0a8f80']
    for ax,(endpoint,name) in zip(axes,NAMES.items()):
        part = m.loc[(m.endpoint == endpoint)&(m.view == 'available')].set_index('model').loc[['v1_default','v1_tuned','v4']]
        values = part.sensitivity.to_numpy()
        error = np.vstack([values-part.sensitivity_ci_low.to_numpy(),part.sensitivity_ci_high.to_numpy()-values])
        ax.bar(range(3),values*100,color=colors,yerr=np.maximum(error,0)*100,capsize=4)
        ax.set_xticks(range(3),['v1, порог 0,5','v1, новый порог','v4'],rotation=15,ha='right')
        ax.set_ylim(0,85)
        ax.set_title(name+f'\nнизких значений: {int(part.positives.iloc[0])}',fontsize=11)
        ax.set_ylabel('Чувствительность, %')
        for i,value in enumerate(values):
            ax.text(i,value*100+5,f'{value:.1%}',ha='center',fontsize=10)
        ax.spines[['top','right']].set_visible(False)
    fig.suptitle('Новый внешний тест: профильные маркеры скрыты\nПороги выбраны только на development; специфичность и precision приведены в отчёте',fontsize=12)
    fig.savefig(output/'sensitivity.png',dpi=160)
    plt.close(fig)
    text = f'''# v4: прогноз низких маркеров по неполным анализам

Дата: 2026-10-05. **Обучение и новый внешний тест завершены.**
На реальных NHANES данных улучшились прогнозы низкого ферритина и PLP при скрытой профильной панели.
B12 остаётся слабой задачей: precision 3,6% в расширенной панели, 2,4% по ОАК.
Веса и пороги выбраны до внешнего теста. Клинические головы/default backend v1 сохранены.
v4 — отдельная исследовательская семья `biochemical_v4`, не замена диагнозов/12 причин анемии.

## Почему изменена постановка

Внешняя проверка v1 показала слабое выявление низких маркеров при отсутствии профильных анализов.
В v4 обучаются цели `low_ferritin`, `low_B12`, `low_PLP` на известных положительных и отрицательных
измерениях реальных людей. Значение выше порога означает «маркер не низкий», не «клинического дефицита нет».
Пропуск эталонного маркера исключает запись для этой цели, не создаёт отрицательную метку.
Все клинические цели NHANES остаются неизвестными. Частично синтетические 840 записей кейса
не используются для fit новых голов; они лежат только за прежним фиксированным v1-компаратором.

Ориентиры: ферритин <15 мкг/л, B12 <200 пг/мл, HPLC PLP <20 нмоль/л.
Обоснование для биохимических ориентиров — [CDC Second Nutrition Report]({protocol['reference_source']});
лабораторные методы и условия применимости дополнительно проверены по компонентам.

## Development и закрытый тест

| Цель | Development | Новый внешний тест | Ограничение |
|---|---|---|---|
| low_ferritin | NHANES 2003–2004 + 2005–2006 | 2007–2008 | Только женщины 18–49 с известным измерением; мужчины/пожилые не проверены |
| low_B12 | NHANES 2011–2012 | 2013–2014 | Возраст ≥20; сопоставимый метод Roche, опубликованные поправленные значения LBDB12 в тесте |
| low_PLP | NHANES 2005–2006 | 2007–2008 | HPLC; энзиматический B6 2003–2004 исключён |

Прежний NHANES 2005–2006 после раскрытия результатов используется как development следующей версии,
а не повторно как её нетронутый тест. Новые тестовые таблицы нормализуются только после сохранения
selection и весов. Их файлы/методы и контрольные суммы закреплены в протоколе до fit.
Отдельные взрослые когорты после общего фильтра: {manifest['nhanes_2007_2008']['rows_after_audit']} в 2007–2008,
{manifest['nhanes_2013_2014']['rows_after_audit']} в 2013–2014; задачи оцениваются на разных подмножествах с известным маркером.
Это не сумма независимых положительных пациентов по задачам: железо и PLP могут измеряться у одного человека.

Фильтр: ≥18 лет (B12≥20), известная беременность исключена, женщины 18–44 с неизвестной беременностью
исключены, для железа только F18–49. Для inference ≥5 поддержанных лабораторных показателей;
extended дополнительно требует хотя бы один поддержанный показатель биохимии.
Точные совпадения полного ОАК с development/кейсом и полные дубликаты в тесте — по 0.
Общего реестра личностей нет, проверка совпадений не доказывает отсутствие общих людей между источниками.
Возраст источника сохраняется, модельный age фиксированно ограничен 80 для сопоставимости top-coded циклов.
Вес WTMEC2YR/страты/PSU сохранён, benchmark невзвешенный, популяционная распространённость не оценивается.

Единицы: Hb/MCHC г/дл×10→г/л, CRP мг/дл×10→мг/л, остальные выбранные SI-поля импортированы документированно.
Использованы [ферритин 2007–2008](https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2007/DataFiles/FERTIN_E.htm),
[PLP 2007–2008](https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2007/DataFiles/VIT_B6_E.htm),
[B12 2013–2014](https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2013/DataFiles/VITB12_H.htm).
У B12 используются уже исправленные CDC значения, без второй коррекции. CBC instrument MAXM→HMX
изменён между ранними циклами, методический сдвиг остаётся ограничением внешнего сравнения.
Фолат 2007–2008 имеет другой метод и не включался; медь/воспаление/12 этиологий в v4 не обучались.

## Модели, пропуски и выбор

Четыре фиксированные конфигурации: логистическая регрессия (C=0,5) с median+scale,
логистическая с дополнительными индикаторами пропусков, CatBoost 200 деревьев depth4,
CatBoost с копиями drop30 и ОАК. Параметры закреплены до обучения, подбора по внешнему тесту нет.
Копии добавляются только после разделения обучающих пациентов. Импутация/масштаб fit только на train.
Возраст/пол не маскируются; пропуски исходника сохраняются, target markers не восстанавливаются.

Для каждой из трёх целей две головы: CBC и extended. CBC — 11 демографических/ОАК признаков;
extended добавляет creatinine, albumin, LDH и CRP для железа/PLP. У B12 CRP не используется:
его нет в соответствующих сравниваемых компонентах. Все нутриентные и профильные маркеры,
MMA/гомоцистеин, идентификаторы, цикл, PSU/веса и диагнозы исключены из предикторов.
Это ограниченная первая версия; другие доступные анализы пока не используются, что явно учитывается в достаточности.

Пять development-fold с группировкой cycle/stratum/PSU; группы и пациенты train/valid не пересекаются.
Сравнение по среднему AP двух OOF-режимов (доступные предикторы и drop30), затем AUROC/log-loss.
OOF выбирает конфигурацию; порог — минимальный с specificity≥90% **в обоих** OOF-режимах.
Это инженерная исследовательская цель, не утверждённая медицинская граница.
Метрики development использованы для выбора и имеют оптимизм отбора; не называются независимой проверкой.
Валидационные fold-метрики порога также не являются полностью вложенной оценкой его выбора.
Независимость оценки новой версии обеспечивается последующим отдельным внешним тестом.
Нативные scores не проходили дополнительной калибровки и не являются подтверждёнными клиническими вероятностями.

{selected_table}

Baseline v1 сравнивается на **тех же пациентах и предикторах**, с прежним порогом 0,5 и с порогами,
подобранными по тому же development-правилу. Внешний тест воспроизводит маршрут extended/CBC по пациенту,
включая смену маршрута после drop30. Case-головы v1 обучены на другой семантике меток; здесь проверяется
перенос их scores на низкий маркер, не клиническая истинность их исходных диагнозов.

Протокол frozen UTC `{protocol['frozen_at_utc']}`, SHA `{sha(output/'protocol.json')}`.
Selection/веса locked UTC `{lock['locked_at_utc']}` до теста; SHA весов `{lock['model_sha256']}`.
После теста веса и пороги не изменены. Показатели теперь открыты: дальнейшее улучшение этой версии
потребует следующего внешнего теста.

## Новый внешний тест: маркеры скрыты, доступны ОАК/поддержанная биохимия

{comparison(m,'available')}

![Чувствительность на новом тесте](sensitivity.png)

Железо: sensitivity 15,5% у v1 default /37,1% у v1 tuned /63,4% у v4,
но specificity соответственно 99,0% /95,6% /91,4%. Есть компромисс: число положительных
прогнозов увеличивается. AP 0,551→0,670 и AUROC 0,824→0,871 относительно прежних scores
поддерживают улучшение ранжирования, а не только эффект смены порога.
Ферритин всё ещё пропущен у 78 из 213 низких значений; модель не заменяет измерение.

PLP: sensitivity 33,1%, specificity 90,3%, precision 33,1%; F1 0,331 против 0,062 у v1 tuned.
AP 0,145→0,322, AUROC 0,504→0,758. Улучшение существенное относительно прежней модели,
но 454 из 679 низких значений всё ещё пропущены.

B12: sensitivity 24,8%, specificity 86,5%, precision **3,6%** (26 true positives, 688 marker-relative false positives).
AUROC лишь 0,589, AP 0,031 при доле низких значений около 2%. Формальный критерий прироста
extended пройден, но абсолютное качество остаётся слабым и не даёт основания для уверенного клинического вывода.
CBC B12 не прошёл критерии прироста. Precision зависит от состава/доли положительных этой выборки;
она не переносится автоматически на клиническую популяцию.

## Только ОАК

{comparison(m,'cbc_only')}

## Дополнительно скрыты 30% реально измеренных предикторов

{comparison(m,'drop30')}

Для железа F1 остаётся 0,580, PLP 0,285. Для B12 sensitivity снижается до 7,6%;
низкая чувствительность/слабое ранжирование сохраняются. Минимальный ввод 5 labs — техническое
ограничение исследования, не доказательство достаточности такого набора анализов.

## Проверки прироста и решение

До теста: ≥30 positives, specificity≥85%, прирост F1≥0,02 относительно v1 tuned,
нижняя граница парного 95% интервала прироста sensitivity >0; extended дополнительно
не должен проиграть v1 tuned по F1 drop30 более чем на 0,02.
Интервалы приблизительные: 500 bootstrap PSU внутри страт, невзвешенные, без поправки на множественные сравнения.
Сравнение sensitivity при разных specificity не заменяет сравнение при клинически согласованной стоимости ошибок;
AP/AUROC и абсолютные числа ошибок приведены дополнительно.

{gate_table}

Пять из шести голов прошли **исследовательский критерий прироста**, не клиническую приёмку.
Критерий допускает слабое абсолютное качество B12; этот результат явно раскрыт и не используется для
переименования низкого маркера в диагноз. Ни одна из 12 причин анемии не валидирована этой серией.
Default клинические case-головы v1 в backend не заменены. Порог v1_tuned используется только
как экспериментальный компаратор и не перенесён в клинический backend.

## Локальный запуск v4

Синтетический пример без профильных маркеров:

```bash
.venv/bin/python -m ml_baselines.biochemical_predict --input examples/biochemical_v4_input.json --pregnancy not_pregnant
```

`load_candidate` проверяет отдельный `backend/data/biochemical_model_trust.json` до joblib-load,
SHA весов и внешней evidence-sidecar, единицы/схему/тип голов и конечность порогов.
Этот реестр не делает v4 допустимым clinical bundle для ModelService.
Ответ содержит low_ferritin/low_B12/low_PLP, research_score, пороги, маршрут и external_gate_passed;
`clinicalValidated=false`. При известном маркере его фактический статус выводится отдельно,
но численный прогноз намеренно остаётся полученным без маркера.
Для женщин 18–44 неизвестная pregnancy приводит к отказу; известная беременность исключена,
для железа мужчины/возраст>49 исключены; <5 поддержанных labs — недостаточно данных.
Работающий сайт/API сохраняют прежнюю модель; интерфейс/фразы не переключены на биохимические scores.

Воспроизведение обучения (новый каталог):

```bash
.venv/bin/python scripts/fetch_nhanes_v4.py
.venv/bin/python -m ml_baselines.train_biochemical freeze --output experiments/NEW_V4
.venv/bin/python -m ml_baselines.train_biochemical train --output experiments/NEW_V4
.venv/bin/python -m ml_baselines.train_biochemical evaluate --output experiments/NEW_V4
MPLCONFIGDIR=/private/tmp/hema-v4-mpl .venv/bin/python -m ml_baselines.biochemical_report --output experiments/NEW_V4
.venv/bin/python -m unittest discover -s tests -p test_biochemical_v4.py -v
```

Прежние результаты/исходники не перезаписываются. Для нового runtime-bundle нужна отдельная явная регистрация
его hash/evidence, как для прежних весов. Открытые NHANES ID/значения не являются данными пользователей сервиса.

Артефакты: protocol+snapshot, selection+lock, 30 групповых split-audit, OOF NPZ, development/fold metrics,
test features/metadata/audit/manifest, 27 строк external_metrics, individual predictions/subgroups,
external_gates/release_decision, weights, график и контрольные суммы. Проверки текущей реализации — `verification.json`.
'''
    (output/'REPORT.md').write_text(text,encoding='utf-8')
    write_json(output/'artifact_hashes.json',{str(path.relative_to(output)):sha(path) for path in output.rglob('*') if path.is_file() and path.name != 'artifact_hashes.json'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    run(parser.parse_args().output)
