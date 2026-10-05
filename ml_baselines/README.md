# Локальное обучение моделей Hema

Исследовательские модели для неполных лабораторных панелей. Исходный CSV и внешние наборы не изменяются. В первой серии используется только CSV кейса, версии библиотек закреплены в `requirements.txt`.

```sh
.venv/bin/python -m pip install -r ml_baselines/requirements.txt
.venv/bin/python -m ml_baselines.train --output experiments/baseline_v1
.venv/bin/python -m unittest discover -s tests -p 'test_ml_baselines.py' -v
```

Нужны Python 3.12 и пакеты requirements.txt. Новый каталог обязателен; существующие результаты не перезаписываются. По умолчанию CSV берётся из пути `case_source.path` словаря `data/feature_dictionary.json`. `--source` позволяет указать копию с тем же SHA-256. Для нового файла сначала повторить аудит и обновить словарь.

Десять конфигураций × семь целей: baseline prior, логистическая регрессия с масками/без явных масок, Extra Trees, CatBoost, CatBoost с обучением на скрытых панелях, три отдельные модели ОАК и контроль только на масках. Параметры фиксированы. Наличие анемии определяется правилом Hb отдельно.

672 development / 168 holdout, пять общих CV-разбиений, 12 сценариев доступности анализов. Выбор производится по development до проверки holdout. Файлы `REPORT.md`, CSV, NPZ и PNG документируют качество и ограничения. В `models/*.joblib` сохраняются все десять конфигураций на development; `selected.joblib` — выбранные модели по задачам для расширенной панели и ОАК.

Локальный исследовательский inference:

```sh
.venv/bin/python -m ml_baselines.predict \
  --bundle experiments/baseline_v1/models/selected.joblib \
  --input patient.json
```

`patient.json` — объект с любым подмножеством 37 признаков в единицах кейса: `{"sex":"F","age_years":42,"hemoglobin":108,"ferritin":9}`. Отсутствующие поля и null сохраняются как NaN; числа не заменяются нулями. Дополнительные/целевые поля, отрицательные и нечисловые значения отклоняются. Hb/пол могут отсутствовать, тогда статус анемии неизвестен. При отсутствии лабораторных значений прогноз не выдаётся. При наличии только показателей ОАК выбирается отдельно обученная ОАК-модель; иначе основной кандидат. Возможен `--panel primary|cbc` для явного режима.

Scores исследовательские и не откалиброваны; низкое значение не исключает дефицит при недостаточных данных. Возрастной диапазон, клинический минимальный набор и отказ от решения нуждаются в дальнейшем согласовании. Нет клинических назначений и автоматического отображения смешанных состояний в 12 классов. Загружать joblib следует только из локально созданных доверенных артефактов.

Выбранные веса также подключены к API по умолчанию: `.venv/bin/python -m backend`. Адаптер `backend/model_service.py` проверяет схему/словарь и добавляет политику воздержания от причинного вывода, версию весов и сохранение отчётов. Подробности и ограничения — в `backend/README.md`. Метрики REPORT.md описывают исходный inference, а не дополнительный слой воздержания API.

## Вторая серия: robust_v2

```sh
.venv/bin/python -m ml_baselines.improve --source data/case/deficiency_anemia.csv --output experiments/robust_v2
```

Используются прежние 672 development/168 holdout и пять fold, шесть новых конфигураций с маскированием, вычисляемыми признаками, весами классов и узкими панелями. Вариант 12 классов согласует оценки с известным статусом Hb. Выбор производится по development с ограничениями регрессий и записывается до повторной проверки holdout. Порог бинарных решений остаётся 0,5; калибровка не выполнялась. Повторно использованный holdout уже был открыт в v1 и не является новой независимой проверкой.

Полный набор v2 улучшил часть задач, но не прошёл проверки перехода по 12 классам исходной панели и среднему F1 дефицитов по ОАК. По умолчанию backend сохраняет v1. Для отдельного исследовательского сравнения:

```sh
.venv/bin/python -m backend --port 8001 --data-dir data/local_v2 --model-bundle experiments/robust_v2/models/selected.joblib
```

Это отдельный сервер и база; врачебная роль использует обычную авторизацию проекта. Набор имеет версию `hema-robust-v2-8f7c2fd5e003`, calibrated=false. Артефакты: REPORT.md, comparison.csv, api_policy_metrics.csv, baseline_error_audit.csv, release_decision.json, веса, контрольные суммы и снимок точных исходников обучения. Старые веса/эксперимент не изменены. Для повторного запуска нужен новый каталог вывода.

## Третья серия: calibrated_v3 и внешние метки

```sh
.venv/bin/python -m ml_baselines.calibrate --output experiments/NEW_NAME
MPLCONFIGDIR=/private/tmp/hema-mpl .venv/bin/python -m ml_baselines.calibration_report --output experiments/NEW_NAME
.venv/bin/python -m unittest discover -s tests -p test_calibration.py
```

Пять внешних фолдов и три внутренних: калибраторы обучаются на внутренних OOF, оцениваются на внешнем validation. Выбор по log loss/Brier/ECE с ограничением падения F1, до повторной проверки holdout. Базовые v1 модели и порог 0,5 сохранены. Финальные калибраторы fit на development OOF; holdout не участвует в fit. Temperature для 12 классов, temperature/sigmoid_logit для бинарных задач. Выбраны 10 из 14 преобразований; четыре головы сохраняют исходные scores. `calibrationApplied=true`, `calibration.status=partial`, общий `calibrated=false`; методы указаны по задачам и панелям, клиническая валидация не установлена.

На повторном holdout primary-классы: ECE 0,260→0,061, log loss 0,609→0,396, macro-F1 остаётся 0,903. Калибровка увеличила долю выданных API-выводов 56,0%→68,5%, совпадение с CSV среди них снизилось 96,8%→93,9%. По политике эксперимента новая независимая размеченная проверка необходима для promotion: default v1 сохранён. Набор v3 доступен отдельно:

```sh
.venv/bin/python -m backend --port 8002 --data-dir data/local_v3 --model-bundle experiments/calibrated_v3/models/selected.joblib
```

Внешний опыт использовал только положительные частичные метки Kılıçarslan и 10 документированных признаков. Маски неизвестных целей, дедупликация, временное исключение QC-флагов и суммарный вес источника 10% case-train. Средний development F1 iron/B12/folate изменился на −0,0005/−0,0021/−0,0098; ни одна задача не прошла критерии замены. Внешние веса сохранены отдельно и к API не подключены; они не используются для калибровки.

NHANES уже нормализован в `data/external/processed/nhanes_case_units_v1/`: 5351 +5807 записей, только документированные соответствия; все цели неизвестны, маски 0. Скрипт `scripts/prepare_nhanes.py` сохраняет циклы, веса/дизайн, LOD, методы и ограничения. Не превращает низкие биомаркеры в подтверждённые диагнозы. Подробности, таблицы, график надёжности, все разбиения и границы интерпретации — `experiments/calibrated_v3/REPORT.md`.

SHA весов v3 зарегистрирован в `backend/data/model_trust.json` перед joblib-load. При создании нового набора его контрольная сумма требует явной регистрации администратором/локальным разработчиком либо `--trusted-model-sha256`; проверка доверия не отключается.

## Внешняя независимая биохимическая проверка

`experiments/external_validation_v1/REPORT.md`: новые 5087 взрослых NHANES 2005–2006, замороженные v1/v2/v3 и порог 0,5, без обучения. Главный режим скрывает профильную панель после создания отдельного ориентира из биомаркера. При отсутствии панели sensitivity v1 для низкого ферритина 16,3%, B12/PLP — 0%; для фолата всего 2 положительных записи. Сам определяющий маркер в available содержит incorporation bias. Низкий score не исключает дефицита; 12 диагнозов клинически не проверены. Веса и default v1 сохранены.

```sh
.venv/bin/python scripts/fetch_nhanes_validation.py
.venv/bin/python -m ml_baselines.external_validate freeze --output experiments/NEW_EXTERNAL_NAME
.venv/bin/python -m ml_baselines.external_validate evaluate --output experiments/NEW_EXTERNAL_NAME
MPLCONFIGDIR=/private/tmp/hema-validation-mpl .venv/bin/python -m ml_baselines.external_validation_report --output experiments/NEW_EXTERNAL_NAME
.venv/bin/python -m unittest discover -s tests -p test_external_validation.py -v
```

Протокол/веса/исполнявшийся код хешируются до оценки; повторная запись результатов запрещена. Индивидуальные scores, метрики, подгруппы, приблизительные bootstrap PSU интервалы и сдвиг распределений сохранены. Все клинические метки остаются неизвестными. После открытия результатов новая версия модели требует следующего внешнего теста. Схема будущей клинической проверки и шаблон — `docs/INDEPENDENT_VALIDATION.md`.

## Реальные данные: biochemical_v4

Обучены отдельные модели **низких маркеров**, не клинических дефицитов: low_ferritin/low_B12/low_PLP, по две панели CBC/extended. В предикторах только ОАК и поддержанная биохимия; целевые и другие нутриентные маркеры исключены. Development NHANES 2003–2006/2011–2012; после выбора алгоритмов/порогов новый тест 2007–2008/2013–2014. Сравнение с v1 при 0,5 и v1_tuned по тому же development-правилу.

На новом тесте при скрытых маркерах F1 железа v1_tuned→v4 0,468→0,616, PLP 0,062→0,331. Specificity v4 91,4%/90,3%, sensitivity 63,4%/33,1%, precision 60,0%/33,1%. B12 extended F1 0,063, precision лишь 3,6%; CBC B12 не прошёл критерий прироста. Пять остальных голов формально прошли исследовательский критерий, который не является клинической приёмкой. 12 причин анемии не проверены; default backend v1 сохранён. Полный отчёт `experiments/biochemical_v4/REPORT.md`.

```sh
.venv/bin/python -m ml_baselines.biochemical_predict --input examples/biochemical_v4_input.json --pregnancy not_pregnant
```

Пример вымышленный. Runtime проверяет отдельный `backend/data/biochemical_model_trust.json` до joblib-load, scope/units/схему и внешнюю evidence. Выдаёт research_score_for_low_biochemical_marker, порог/маршрут и external_gate_passed, clinicalValidated=false. Для железа поддержана только F18–49; pregnancy/unknown pregnancy в F18–44 и <5 поддержанных labs — отказ. Исходные веса clinical модели/пороги не менялись; v4 не зарегистрирован в клиническом model_trust.json. Inference по B12 с непройденным CBC gate остаётся явно экспериментальным.

```sh
.venv/bin/python scripts/fetch_nhanes_v4.py
.venv/bin/python -m ml_baselines.train_biochemical freeze --output experiments/NEW_V4
.venv/bin/python -m ml_baselines.train_biochemical train --output experiments/NEW_V4
.venv/bin/python -m ml_baselines.train_biochemical evaluate --output experiments/NEW_V4
MPLCONFIGDIR=/private/tmp/hema-v4-mpl .venv/bin/python -m ml_baselines.biochemical_report --output experiments/NEW_V4
.venv/bin/python -m unittest discover -s tests -p test_biochemical_v4.py -v
```

24 комбинации конфигурация/цель/панель, 120 fit на пяти development-fold +6 финальных fit. Группы PSU/пациенты не пересекаются, искусственные копии только train. Исходники/weights/selection/thresholds закреплены до теста; старые каталоги не перезаписываются. Новый runtime-bundle регистрируется отдельно с его SHA/evidence. После раскрытия теста следующая версия требует нового внешнего источника.
