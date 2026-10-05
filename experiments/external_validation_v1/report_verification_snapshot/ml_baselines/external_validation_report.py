"""Render the completed frozen biochemical benchmark, without changing its protocol."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .external_validate import OUTPUT, sha, write_json

ENDPOINT_NAMES = {'low_ferritin': 'Низкий ферритин', 'low_B12': 'Низкий B12',
                  'low_folate': 'Низкий фолат*', 'low_PLP': 'Низкий PLP (B6)'}
VIEW_NAMES = {'available': 'С маркером', 'reference_panel_withheld': 'Без профильной панели', 'cbc_only': 'Только ОАК'}


def table(metrics, model, view):
    result = ['| Биохимический ориентир | n / низкий маркер | F1 | Чувствительность | Специфичность | Precision |',
              '|---|---:|---:|---:|---:|---:|']
    for endpoint, name in ENDPOINT_NAMES.items():
        row = metrics.loc[(metrics.model == model) & (metrics.view == view) & (metrics.endpoint == endpoint)].iloc[0]
        precision = 'не определён' if pd.isna(row.precision) else f'{row.precision:.3f}'
        result.append(f'| {name} | {row.n:,} / {row.positives:,} | {row.F1:.3f} | {row.sensitivity:.1%} | {row.specificity:.1%} | {precision} |')
    return '\n'.join(result)


def run(output=OUTPUT):
    m = pd.read_csv(output / 'metrics.csv')
    cohort = json.loads((output / 'cohort_manifest.json').read_text())
    protocol = json.loads((output / 'protocol.json').read_text())
    models = list(protocol['models'])
    columns = [(view, model) for view in protocol['views'] for model in models]
    matrix = np.array([[m.loc[(m.endpoint == endpoint) & (m.view == view) & (m.model == model), 'F1'].iloc[0]
                        for view, model in columns] for endpoint in ENDPOINT_NAMES])
    fig, ax = plt.subplots(figsize=(13, 4.8), layout='constrained')
    im = ax.imshow(matrix, vmin=0, vmax=1, cmap='Blues', aspect='auto')
    ax.set_xticks(range(len(columns)), [f'{VIEW_NAMES[v]}\n{model}' for v, model in columns], rotation=25, ha='right', fontsize=9)
    ax.set_yticks(range(4), list(ENDPOINT_NAMES.values()))
    ax.set_title('NHANES 2005–2006: F1 относительно низкого маркера, порог 0,5\nЭто биохимические ориентиры, не подтверждённые диагнозы', fontsize=12)
    for i in range(4):
        for j in range(len(columns)):
            ax.text(j, i, f'{matrix[i,j]:.3f}', ha='center', va='center', color='white' if matrix[i,j] > .55 else 'black', fontsize=10)
    ax.axvline(2.5, color='grey', lw=1)
    ax.axvline(5.5, color='grey', lw=1)
    fig.colorbar(im, ax=ax, label='F1')
    fig.text(.01, .005, '*Фолат: только 2 положительных записи — чувствительность и ранжирование моделей неустойчивы.', fontsize=9)
    fig.savefig(output / 'external_quality.png', dpi=160)
    plt.close(fig)
    rows = []
    for model in models:
        for view in protocol['views']:
            part = m[(m.model == model) & (m.view == view)].set_index('endpoint')
            rows.append('| ' + ' | '.join([model, VIEW_NAMES[view], *[f'{part.at[e,"F1"]:.3f}' for e in ENDPOINT_NAMES]]) + ' |')
    comparison = '\n'.join(['| Модель | Вход | Ферритин F1 | B12 F1 | Фолат F1* | PLP F1 |', '|---|---|---:|---:|---:|---:|', *rows])
    report = f'''# Независимая внешняя проверка: исследовательская версия

Дата: 2026-10-05. Проверка завершена; **клиническая финализация не подтверждена**.
Зафиксированный champion — `baseline_v1`, кандидаты `robust_v2` и `calibrated_v3` оценены для сравнения.
Default, веса, калибраторы и порог 0,5 сохранены. Ни одна модель не обучалась на участниках этого цикла NHANES.
Новая выборка не входит в 840 записей кейса; её результаты ранее не использовались для выбора моделей.
Результаты теперь открыты: любые дальнейшие изменения с учётом этой проверки требуют новой проверочной выборки.

## Что и как проверено

Официальный [NHANES 2005–2006](https://wwwn.cdc.gov/Nchs/Nhanes/Search/DataPage.aspx?Component=Laboratory&Cycle=2005-2006):
{cohort['source_rows']:,} участников, {cohort['adults']:,} взрослых. После исключения {cohort['known_pregnancy_adults_excluded']} взрослых с известной беременностью
и {cohort['unknown_pregnancy_women_18_44_excluded']} женщин 18–44 лет с неизвестным статусом осталось **{cohort['final_rows']:,} человек**.
Возраст 85+ сохранён как top-coded, не восстановлен по среднему. Соединения компонентов по SEQN один-к-одному.
Дубликаты полных векторов и точные совпадения полного ОАК с данными кейса: по 0. Это проверка совпадений,
а не подтверждение личности: общий реестр пациентов отсутствует. Исходные файлы не изменялись.

24 признака имеют документированные отображения; все отсутствующие показатели остаются NaN.
Hb и MCHC г/дл ×10 → г/л, CRP мг/дл ×10 → мг/л; ферритин мкг/л, B12 пг/мл,
сывороточный фолат нг/мл и сывороточный PLP нмоль/л импортируются без изменения числа.
Нет замены общего билирубина непрямым, нет подстановки RBC-фолата вместо сывороточного, нет расчёта отсутствующего MMA.
Методы B12/фолата — Bio-Rad, PLP — HPLC. Единицы совпадают с кейсом, но метод анализов в исходном кейсе неизвестен.

Протокол и код сохранены **до вычисления предсказаний**, время — `{protocol['frozen_at_utc']}`,
SHA-256 протокола — `{sha(output / 'protocol.json')}`. Хеши доверенных весов проверяются до joblib-десериализации.
Подбор порогов, калибровка, импутация по внешней выборке и обучение отсутствуют.

Биохимические ориентиры из [CDC Second Nutrition Report (2012)]({protocol['reference_source']}):
ферритин <15 мкг/л, B12 <200 пг/мл, фолат Bio-Rad <2 нг/мл, PLP HPLC <20 нмоль/л.
Их названия отделены от целей кейса: `low_ferritin`, `low_B12`, `low_folate`, `low_PLP`.
Пропуск маркера означает неизвестный ориентир, а не отрицательную метку; значение выше порога означает
«маркер не низкий», а не «дефицита нет». Все клинические метки сохранены пустыми, маски известных целей — 0.
Низкий маркер не определяет причину анемии и не исключает смешанные состояния или воспаление.
Взрослый ферритин в этом цикле измерялся **только у женщин 12–49 лет** — у нас взрослая подгруппа 18–49;
результаты железа нельзя распространить на мужчин и пожилых. См. [FERTIN_D](https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2005/DataFiles/FERTIN_D.htm).

Главный режим — **без профильной панели**: после создания эталонного ориентира из входа удаляются все показатели
соответствующего `PANELS`, включая MMA/гомоцистеин для B12 и гомоцистеин для фолата.
Дополнительно проверены доступные показатели с включённым маркером и только ОАК.
В каждом режиме нужно ≥5 измеренных лабораторных показателей; числа исключений сохранены.
Маршрут primary/CBC выбирается для каждого пациента так же, как в `predict(auto)`.
Это оценка численных ML-голов; отдельные причины отказа интерфейса/вердикта не считаются диагностическими метками.

Метрики невзвешенные, отражают этот benchmark, не популяционную распространённость.
WTMEC2YR, страты и PSU сохранены; приближённые 95% интервалы F1/sensitivity/specificity/precision —
500 повторов bootstrap PSU внутри страт, с сохранением кластеров. AUC/AP без интервалов.
Пустой precision — нет положительных прогнозов; не подменяется 0.
При нулевых истинноположительных прогнозах bootstrap может дать [0,0]: это свойство текущей выборки,
а не доказательство точного нулевого значения на будущих пациентах. Редкие положительные подгруппы отдельно отмечены.
Brier/log-loss/ECE сравнивают scores с **биохимическим ориентиром**; они не подтверждают калибровку клинических вероятностей.

## Основной результат baseline_v1: профильная панель скрыта

{table(m, 'baseline_v1', 'reference_panel_withheld')}

Ферритин: 32 из 196 низких значений обнаружены, 164 пропущены относительно ориентира;
F1 0,270 (приближённый 95% интервал 0,218–0,314), sensitivity 16,3% (12,7–19,6%).
B12: 0 из 130; PLP: 0 из 625. Это не проверка всех клинических дефицитов, но она
**не поддерживает обещание надёжного выявления соответствующих низких маркеров без профильных анализов**.
Для фолата всего 2 положительных записи; вывод о переносимости/превосходстве какой-либо модели невозможен.

## Дополнительный режим: только ОАК

{table(m, 'baseline_v1', 'cbc_only')}

Высокая специфичность при почти нулевой чувствительности не означает хорошее обнаружение дефицита.
Подгруппы по полу, возрасту и статусу Hb доступны в `subgroup_metrics.csv`; у железа мужская подгруппа отсутствует.
У фолата и некоторых подгрупп слишком мало положительных записей для надёжных сравнений.

## Дополнительный режим: определяющий маркер доступен модели

{table(m, 'baseline_v1', 'available')}

PLP F1 0,794 у v1 и 0,828 у v3, но модель получает сам маркер, определяющий ориентир.
Такая согласованность полезна для проверки импорта/поведения, однако содержит incorporation bias.
Расхождение ML-оценки железа с ферритином ≥15 нельзя назвать ложным клиническим диагнозом:
модель кейса учитывает и другие железные показатели, а proxy имеет более узкий смысл.

## Сравнение всех трёх зафиксированных моделей

{comparison}

![F1 по режимам](external_quality.png)

Калибровка на данных кейса не устранила падение при отсутствии панелей. У v2 высокий F1 B12
с известным B12 (0,904), однако без панели F1 остаётся 0. Этот результат не даёт основания
выбирать v2 для неполных анализов или объявлять её клинически проверенной.
Сравнение с постоянным prior из **672 обучающих пациентов кейса** есть в metrics.csv.
Для ферритина у v1 Brier без панели 0,115; для PLP 0,134 — почти всегда низкие scores.
Распределения существенно различаются: медиана Hb 111,7 г/л в кейсе против 144,0 здесь,
B12 307 против 478,5 пг/мл; подробности в `distribution_shift.csv`.
Разницу могут объяснять состав участников, искусственность исходной выборки, лабораторные методы,
процесс назначения анализов и разный смысл меток; причинное объяснение из этих данных не установлено.

## Решение по релизу

Исследовательский champion `baseline_v1` остаётся в backend. Это сохранение зафиксированного состояния,
**а не утверждение его внешней клинической достоверности**. Ни одну из трёх моделей нельзя финализировать
как надёжную модель 12 причин анемии на основании этой проверки; число независимо проверенных причин — 0/12.
`clinicalValidated=false`, `independentClinicalValidation=false`, `weights_changed=false`.
Не использовать низкий score как исключение дефицита при отсутствующих профильных анализах.
Внешние клинические метки меди и воспалительной анемии здесь отсутствуют и не создавались.

Обоснованный следующий исследовательский шаг — разделить обнаружение измеренного низкого маркера
и прогноз при отсутствующем маркере; для второго нужны дополнительные реальные обучающие пациенты
и отдельный следующий внешний тест. Это предложение, а не выполненное переобучение.
Для врачебной валидации подготовлен `docs/INDEPENDENT_VALIDATION.md` с условиями независимости
и шаблоном adjudication; пользователь подтвердил, что сейчас доступны только открытые данные.

## Воспроизведение и артефакты

```bash
.venv/bin/python scripts/fetch_nhanes_validation.py
.venv/bin/python -m ml_baselines.external_validate freeze --output experiments/external_validation_reproduction
.venv/bin/python -m ml_baselines.external_validate evaluate --output experiments/external_validation_reproduction
MPLCONFIGDIR=/private/tmp/hema-validation-mpl .venv/bin/python -m ml_baselines.external_validation_report --output experiments/external_validation_reproduction
.venv/bin/python -m unittest discover -s tests -p test_external_validation.py -v
```

Старый каталог результатов нельзя перезаписывать; fetch сохраняет существующие исходники.
`protocol.json`/`protocol.sha256` — правила и веса до проверки, `source_snapshot/` — выполнявшийся код,
`cohort_manifest.json`/`cohort_audit.csv` — источник/исключения/хеши,
`features.csv` — 37 входов в единицах кейса с NaN,
`biochemical_references.csv` — отдельные ориентиры,
`clinical_labels_unknown.csv` — неизвестные клинические цели с масками,
`predictions.csv` — индивидуальные scores, `metrics.csv` — 48 итоговых строк,
`subgroup_metrics.csv` — подгруппы, `distribution_shift.csv` — сдвиг данных,
`release_decision.json` — решение, `artifact_hashes.json` — контрольные суммы.
Открытые NHANES значения не являются данными пользователей сервиса.
'''
    (output / 'REPORT.md').write_text(report, encoding='utf-8')
    write_json(output / 'artifact_hashes.json', {str(path.relative_to(output)): sha(path)
        for path in sorted(output.rglob('*')) if path.is_file() and path.name != 'artifact_hashes.json'})


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    run(parser.parse_args().output)
