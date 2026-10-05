"""Generate auditable tables and standalone plots from saved experiment outputs."""

import json
import hashlib
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from .core import DEFICIENCIES, TARGETS


def table(frame):
    def fmt(value):
        return f'{value:.3f}' if isinstance(value, (float, np.floating)) else str(value)
    return '\n'.join(['| '+' | '.join(frame.columns)+' |',
                      '| '+' | '.join(['---']*len(frame.columns))+' |']+
                     ['| '+' | '.join(fmt(v) for v in row)+' |' for row in frame.itertuples(index=False,name=None)])


def write_report(output):
    output=Path(output)
    manifest=json.loads((output/'manifest.json').read_text())
    manifest['report_generator_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    selection=json.loads((output/'selection.json').read_text())
    metrics=pd.read_csv(output/'metrics.csv')
    cv=metrics[metrics.partition.eq('development_oof')]
    test=metrics[metrics.partition.eq('holdout')]
    primary=test[test.apply(lambda r:r.config==selection[r.target]['primary'],axis=1)]
    cbc=test[test.apply(lambda r:r.config==selection[r.target]['cbc'],axis=1)]
    summary=cv[cv.target.isin(DEFICIENCIES)].pivot_table(index='config',columns='scenario',values='f1',aggfunc='mean')
    order=['available','drop30','drop60','cbc_only','hb_only']
    summary=summary[order].sort_values('available',ascending=False)
    fig,ax=plt.subplots(figsize=(10,6))
    plot=ax.imshow(summary.to_numpy(),vmin=0,vmax=1,cmap='YlGnBu',aspect='auto')
    ax.set_xticks(range(len(order)),['Available','30% removed','60% removed','CBC only','Hb only'],rotation=20,ha='right')
    ax.set_yticks(range(len(summary)),summary.index)
    for i,row in enumerate(summary.to_numpy()):
        for j,value in enumerate(row):
            ax.text(j,i,f'{value:.2f}',ha='center',va='center',color='white' if value>.65 else '#103648')
    ax.set_title('Development OOF: mean F1 across five deficiencies (n=672)')
    fig.colorbar(plot,ax=ax,label='Mean F1'); fig.tight_layout()
    fig.savefig(output/'development_comparison.png',dpi=160);plt.close(fig)
    matrix=primary.pivot(index='target',columns='scenario',values='f1').reindex(TARGETS)
    order=['available','drop30','drop60','cbc_only','hb_only','no_iron','no_B12','no_folate','no_B6','no_copper','no_hemoglobin','no_demographics']
    matrix=matrix[order]
    fig,ax=plt.subplots(figsize=(14,6))
    plot=ax.imshow(matrix.to_numpy(),vmin=0,vmax=1,cmap='YlGnBu',aspect='auto')
    ax.set_xticks(range(len(order)),order,rotation=45,ha='right');ax.set_yticks(range(len(matrix)),matrix.index)
    for i,row in enumerate(matrix.to_numpy()):
        for j,value in enumerate(row):
            ax.text(j,i,f'{value:.2f}',ha='center',va='center',color='white' if value>.65 else '#103648',fontsize=9)
    ax.set_title('Locked holdout: selected models under missing inputs (n=168)\nMulticlass macro-F1; binary positive-class F1; threshold 0.5')
    fig.colorbar(plot,ax=ax,label='F1');fig.tight_layout()
    fig.savefig(output/'holdout_missingness.png',dpi=160);plt.close(fig)
    confusion=json.loads((output/'holdout_confusion.json').read_text())
    key=f"{selection['anemia_class']['primary']}__anemia_class__available"
    item=confusion[key]
    counts=np.asarray(item['confusion_matrix'])
    fig,ax=plt.subplots(figsize=(12,10))
    plot=ax.imshow(counts,cmap='Blues')
    ax.set_xticks(range(len(counts)),item['labels'],rotation=70,ha='right',fontsize=9)
    ax.set_yticks(range(len(counts)),item['labels'],fontsize=9)
    for i,row in enumerate(counts):
        for j,count in enumerate(row):
            if count: ax.text(j,i,str(count),ha='center',va='center',color='white' if count>counts.max()*.6 else '#103648')
    ax.set_xlabel('Predicted');ax.set_ylabel('True');ax.set_title('12-class holdout confusion matrix; available inputs')
    fig.colorbar(plot,ax=ax);fig.tight_layout();fig.savefig(output/'holdout_confusion.png',dpi=160);plt.close(fig)
    # Reliability diagnostics of raw scores. No calibrator has been fitted.
    source=pd.read_csv(manifest['source_path'])
    split=pd.read_csv(output/'splits.csv'); idx=split.loc[split.partition.eq('holdout'),'row_index'].to_numpy()
    pstore=np.load(output/'holdout_probabilities.npz')
    fig,axes=plt.subplots(2,3,figsize=(12,7))
    for target,ax in zip(TARGETS[1:],axes.flat):
        p=pstore[f"{selection[target]['primary']}__{target}__available"][:,1]
        true,pred=calibration_curve(source.iloc[idx][target],p,n_bins=5,strategy='quantile')
        ax.plot([0,1],[0,1],':',color='gray');ax.plot(pred,true,'o-',color='#147d73')
        ax.set(xlim=(0,1),ylim=(0,1),title=f'{target}\npositives={int(source.iloc[idx][target].sum())}',xlabel='Mean raw score',ylabel='Observed fraction')
    fig.suptitle('Holdout reliability diagnostics: uncalibrated scores, small rare-class samples')
    fig.tight_layout();fig.savefig(output/'holdout_reliability.png',dpi=160);plt.close(fig)
    selected_table=pd.DataFrame([{'target':t, **s} for t,s in selection.items()])
    multiclass=cv[cv.target.eq('anemia_class')].pivot(index='config',columns='scenario',values='f1')[['available','drop30','drop60','cbc_only','hb_only']]
    natural=primary[primary.scenario.eq('available')][['target','config','n','positive_n','f1','precision','recall','specificity','pr_auc','f1_ci_low','f1_ci_high']]
    cbc_table=cbc[cbc.scenario.eq('available')][['target','config','f1','recall','pr_auc']]
    mask=cv[cv.config.eq('missingness_only')&cv.scenario.eq('available')][['target','f1','pr_auc','recall']]
    available_scores=primary[primary.scenario.eq('available')].set_index('target')
    removed_scores=primary[primary.scenario.eq('drop60')].set_index('target')
    decisions='\n'.join(f"- `{t}`: `{s['primary']}` для основной панели, `{s['cbc']}` для ОАК. Holdout F1 основной модели: {available_scores.at[t,'f1']:.3f}; при удалении 60% результатов: {removed_scores.at[t,'f1']:.3f}." for t,s in selection.items())
    support=pd.read_csv(output/'per_class.csv')
    support=support[support.partition.eq('holdout')&support.target.eq('anemia_class')&support.scenario.eq('available')&support.config.eq(selection['anemia_class']['primary'])]
    mixed=pd.read_csv(output/'mixed_metrics.csv')
    subgroup=pd.read_csv(output/'subgroups.csv')
    no_anemia=subgroup[subgroup.scenario.eq('available')&subgroup.group.eq('no_anemia')][['target','n','positive_n','f1','precision','recall']]
    text=f'''# Первые локальные модели Hema

Эксперимент {manifest['experiment_version']}, завершён {manifest['finished_at']}. Исходный CSV {manifest['n']} строк; SHA-256 `{manifest['source_sha256']}`.

## Метод

- Только исходный CSV кейса; внешние данные и дополнительные пациенты не использованы.
- 672 пациента development, 168 holdout (20%, стратификация по 12 классам). Пять общих development-разбиений. `splits.csv` содержит исходные индексы строк, -1 означает holdout.
- Все десять конфигураций и семь задач используют одинаковые разбиения. Идентификаторы и все целевые столбцы исключены из входов.
- Параметры заданы до оценки; порог бинарных решений 0,5. Нет подбора на holdout, early stopping или калибровки. Выбор сделан по development OOF до открытия holdout. Сравнение и выбор на одних OOF означает, что CV-оценка выбранного победителя может быть оптимистичной; holdout служит отдельной проверкой.
- Основной критерий: средний F1 на доступных данных, при удалении 30% и 60% измеренных лабораторных результатов. Для 12 классов используется macro-F1, для бинарных задач — F1 положительного класса. ОАК-кандидат выбирается отдельно среди трёх моделей по ОАК.
- CatBoost работает с числовыми NaN. Логистическая регрессия и Extra Trees используют медианы обучающей части и маски пропусков. В `logistic_values` явной маски нет, но импутация всё равно может оставлять след наличия анализа. `missingness_only` видит только наличие признаков, без значений и без правила Hb.
- `catboost_dropout`: пять представлений каждого обучающего пациента — исходное, удаление 30%, удаление 60%, ОАК, удаление одной случайной панели. Они создаются после разделения пациентов и никогда не пересекают границу train/validation. Это моделирование пропусков, не новые независимые пациенты.
- Число анализов не фиксировано. «Доступные данные» — исходная неполная панель CSV. Drop30/60 удаляет указанную долю уже присутствующих лабораторных результатов, иногда включая Hb, сохраняя возраст и пол. `cbc_only` содержит возраст, пол и девять показателей ОАК; `hb_only` — возраст, пол, Hb. Остальные сценарии удаляют именованные панели или демографию.
- Анемия по Hb — отдельное правило, не обучаемая цель. Для inflammation_anemia при известных нормальных Hb/поле score=0; при пропусках остаётся исследовательский прогноз. Модель обучена на всей выборке, оценивается как метка воспалительной анемии, не любого воспаления.
- 95% интервалы F1 — 300 повторов bootstrap пациентов holdout; это приблизительная неопределённость выборки, не доказательство переносимости. В development приводится также разброс F1 по пяти разбиениям.

## Выводы первой серии

{decisions}

Выбор различается по целям: одной универсальной победившей модели нет. CatBoost со скрытием данных выигрывает по критерию устойчивости для железа/B12/фолатов/воспалительной анемии; исходный CatBoost — для B6/меди; Extra Trees — для 12 классов. Все эти решения зафиксированы по development, не по таблице holdout.

Для продукта предлагаем раздельные режимы расширенной панели и ОАК. При отсутствии нужной панели особенно осторожно трактовать фолаты, B6 и медь: нулевой F1 при пороге 0,5 не означает отсутствие состояний у пациентов. Он означает, что модель не обнаружила положительные случаи в данном эксперименте. Подбор иных порогов и калибровка — следующий эксперимент на development с отдельной последующей проверкой. Финальный выбор требует проверки на реальных данных.

## Development: пять дефицитов

Среднее пяти бинарных F1. Эти значения не являются accuracy.

{table(summary.reset_index())}

![Сравнение development](development_comparison.png)

## Development: 12 классов

{table(multiclass.reset_index())}

## Выбор, зафиксированный до holdout

{table(selected_table)}

## Holdout: выбранные основные модели, исходная панель

Для anemia_class F1 означает macro-F1, positive_n/precision/recall не определены. Отдельные классы приведены ниже. Для бинарных задач — F1, precision, sensitivity, specificity и average precision (PR-AUC).

{table(natural.fillna('—'))}

## Holdout: отдельно обученные модели ОАК

{table(cbc_table.fillna('—'))}

## Неполный ввод на holdout

Основные выбранные модели применены к сокращённым входам; это стресс-тест. Строка cbc_only для них не заменяет результат отдельно обученных ОАК-моделей.

{table(matrix.reset_index())}

![Пропуски holdout](holdout_missingness.png)

## Классы и смешанные состояния

{table(support[['label','support','precision','recall','f1']])}

![Матрица ошибок](holdout_confusion.png)

Смешанный результат — минимум два положительных решения отдельных моделей, не вероятность сочетания. Категория mixed без анемии отсутствует в исходном CSV; это ограничение данных, не клинический запрет.

{table(mixed[mixed.partition.eq('holdout')][['scenario','positive_n','f1','precision','recall']])}

## Дефициты при нормальном Hb

{table(no_anemia)}

## Контроль зависимости от назначения анализов

Модель только на маске наличия признаков, development OOF:

{table(mask.fillna('—'))}

Высокий результат этого контроля означает, что паттерн назначенных исследований информативен в данной выборке. Он не является диагностическим правилом. Искусственное скрытие проверяет один вид сдвига; реальные механизмы пропусков могут отличаться.

## Вероятности и ограничения

![Проверка калибровки](holdout_reliability.png)

- Scores пока не откалиброваны. Brier/log loss и reliability plots сохранены для оценки, но низкий Brier сам по себе не доказывает хорошую калибровку.
- Всего по 35 положительных пациентов B6/меди; на holdout по 7. Их полнота и интервалы нестабильны. Один seed/holdout — первая серия, а не окончательное ранжирование.
- При идеальных либо полностью нулевых решениях bootstrap-интервал может выродиться в [1,1] или [0,0]. Это свойство наблюдённой выборки, не доказательство отсутствия ошибки вне неё.
- Данные частично синтетические, происхождение разметки неизвестно. Качество здесь измеряет совпадение с CSV, не клиническую точность.
- Отсутствие анализов не равно норме. При пустой лабораторной панели inference возвращает «недостаточно данных». Для прочих неполных панелей сохраняются исследовательские scores и предупреждения; клинический минимальный набор и пороги отказа ещё не утверждены.
- Прямой классификатор классов и независимые дефициты могут расходиться. В этой серии они сравниваются отдельно и не объединяются в клинический диагноз.
- Модели сохранены после обучения на 672 development-пациентах, holdout не включён в финальный fit. Бэкенд и интерфейс не переключены на эти модели.

## Артефакты и воспроизведение

`manifest.json` — версии, схема, хеши и параметры; `metrics.csv` — все сценарии и fold-статистика; `per_class.csv` — классы; `selection.json` — выбор; `selection_scores.csv` — критерий выбора; `oof_probabilities.npz` и `holdout_probabilities.npz` — вероятности в порядке splits.csv; `subgroups.csv` — пол/анемия; `mixed_metrics.csv` — смешанные решения; `models/` — десять наборов и selected.joblib.

Запуск: `.venv/bin/python -m ml_baselines.train --output experiments/baseline_v2`. Существующая папка эксперимента никогда не перезаписывается. Описание inference и зависимостей — `ml_baselines/README.md`.

Документация: [CatBoost NaN](https://catboost.ai/docs/en/concepts/algorithm-missing-values-processing), [imputation pipelines](https://scikit-learn.org/stable/modules/impute.html), [cross-validation](https://scikit-learn.org/stable/modules/cross_validation.html), [calibration](https://scikit-learn.org/stable/modules/calibration.html).
'''
    (output/'REPORT.md').write_text(text,encoding='utf-8')


if __name__=='__main__':
    import sys
    write_report(Path(sys.argv[1]))
