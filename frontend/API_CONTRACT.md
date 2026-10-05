# Hema — реализованный контракт локального API 0.1.0

Реализация: `../backend/`. Запуск из корня: `.venv/bin/python -m backend`. Сайт и API: http://127.0.0.1:8000/ ; OpenAPI JSON: http://127.0.0.1:8000/openapi.json . `src/config.js` включает API на localhost/127.0.0.1; dev-сервер 5173 обращается к 8000. Ошибки не подменяются демонстрацией. Роль определяет представление; врачебный режим дополнительно требует серверной аутентификации.

## Вход врача

`GET /api/auth/session` возвращает `{authenticated, user}`. `POST /api/auth/login` принимает `{username, password}`, устанавливает HttpOnly/SameSite=Strict cookie до 8 часов и возвращает профиль (id, username, displayName, expiresAt). `POST /api/auth/logout` завершает текущий сеанс. Пароли/хеши/токены в JSON не возвращаются. POST с сеансом и auth-запросы требуют `X-Hema-Client: 1`, fetch использует `credentials: 'include'`. Неверные данные → 401; повторные попытки → 429. Самостоятельной регистрации нет.

Вход требуется для doctor/predict и врачебных catalog/preview/data-sufficiency/recommendations, а также GET врачебного отчёта. Новый отчёт содержит doctorId; чужая учётная запись получает 404. PDF принимает дополнительное multipart-поле audience=doctor|patient (по умолчанию patient): врачебное наблюдение и его исходные файлы защищены тем же владельцем. Данные для администрирования: [AUTH.md](../backend/AUTH.md).

## Отчёты

`POST /api/patient/predict`, `POST /api/doctor/predict`

```json
{"inputs":{"age_years":42,"sex":"F","hemoglobin":108,"ferritin":9},"observationId":null}
```

Фронтенд передаёт все 37 входов, пропуски `null`. Сервер принимает неполный словарь, но требует пол F/M, целый возраст 18–120 и Hb. Числа конечные, неотрицательные, без строк/булевых значений. Неизвестные/целевые поля и patient_id отклоняются (422). Единицы — `../data/feature_dictionary.json`; CSV/XLSX должны уже содержать эти единицы.

Текущий ответ (идентификаторы — UUID):

```json
{
  "reportId":"b63b19fe-614e-4af5-a40a-8fedc4b39de8",
  "audience":"doctor",
  "anemia":true,
  "hemoglobin":108,
  "threshold":120,
  "modelConnected":true,
  "modelVersion":"hema-baseline-v1-b413ce1e45d6",
  "modelPanel":"primary",
  "modelDecisionState":"suppressed_sparse",
  "researchOnly":true,
  "calibrated":false,
  "source":"api",
  "prediction":{"code":null,"label":"Недостаточно анализов для оценки причины","hiddenDeficitLabel":"Дефициты не оценены: данных недостаточно."},
  "deficiencyProbabilities":[],
  "anemiaProbabilities":[],
  "warnings":["Модели исследовательские, scores не откалиброваны. Отсутствие анализа не считается нормой."],
  "observationId":null,
  "observationRevision":null
}
```

Также возвращаются inputs, missing, createdAt, method, unitDictionaryVersion. Правило кейса: Hb строго <120 для F и <130 для M. Подключены выбранные обученные модели baseline_v1; версия включает SHA-256 весов. При достаточном вводе возвращаются `modelVerdict: {anemiaClass, deficits, inflammation, status}`, `prediction: {code,label,hiddenDeficitLabel}`, `selectedModels`, `deficiencies`, `decisionReasonCodes`, `decisionThreshold: 0.5` и списки оценок `{code,label,probability}`. `probability` — исследовательская оценка 0–1, не откалиброванная клиническая вероятность. `modelPanel` — `cbc` для ввода только ОАК, иначе `primary`. `modelDecisionState` принимает evaluated/uncertain/inconsistent/suppressed_sparse. Неполный ввод принимается; при менее 5 лабораторных значениях оценки и причины подавляются, как в примере выше. Неопределённый или противоречивый вердикт не активирует специфические рекомендации. Низкие оценки не исключают дефицит. UI экранирует текст и показывает оценки анемических классов только при анемии.

`GET /api/reports/{reportId}` возвращает сохранённый отчёт.

## PDF и подтверждение

`POST /api/observations/pdf`: multipart/form-data, повторяющееся поле files. Весь пакет — одно наблюдение, даты могут различаться. До 10 файлов, 20 МиБ на файл, 60 МиБ PDF всего, 100 страниц на документ. Один пакет одновременно; таймаут 180 секунд.

```json
{"observationId":"b63b19fe-614e-4af5-a40a-8fedc4b39de8","revision":1,"inputs":{"hemoglobin":108,"ferritin":9},"warnings":["Проверьте значения и даты"],"observation":{}}
```

Фактический inputs содержит все 37 полей, демографию null. observation — полное наблюдение: документы, измерения, provenance, исходные/нормализованные значения и единицы, даты, давность, политика и ошибки. Пересчитываются совместимые единицы по подтверждённому словарю. Повторы, неизвестные единицы, границы < / >, устаревшие по политике и будущие результаты не подставляются. Клинических сроков по умолчанию нет; неизвестные даты отмечаются.

Первый клик распознаёт и открывает ручную форму; второй после проверки отправляет:

```json
{"inputs":{"age_years":42,"sex":"F","hemoglobin":109},"observationId":"b63b19fe-614e-4af5-a40a-8fedc4b39de8","observationRevision":1,"reviewedObservation":true}
```

Сервер сохраняет проверенные входы отдельно, увеличивает ревизию и возвращает её в отчёте. Отсутствующее подтверждение или устаревшая ревизия — 409. Адаптер запоминает ревизию в памяти страницы; повторный расчёт использует новую.

- GET /api/observations/{id} — полное наблюдение и последняя подтверждённая форма (до проверки — черновик).
- GET /api/observations/{id}/documents/{document_id} — исходный PDF, например doc-1.
- PUT /api/observations/{id}/review — {revision: 1, inputs: {...}}, сохранить форму без расчёта. Ответ как GET, ревизия увеличена.

Детальный интерфейс выбора повторных измерений по источнику ещё предстоит сделать; кандидаты уже доступны в API. Подтверждение не меняет исходные строки/PDF.

## Рекомендации

POST /api/recommendations, {reportId: UUID, audience: doctor|patient}. Роль должна совпадать с сохранённым отчётом (иначе 409).

По умолчанию используются два локальных каталога по 20 фраз со статусом `draft`. Ответ содержит `items`, `conclusions`, `insufficientData`, `dataSufficiency`, `verdictContext`, `phraseFileVersion`, `clinicalReviewStatus`, `warnings`. Специфические ветки выбираются только при структурированном согласованном вердикте `evaluated`. Версия каталога `phrases-draft-1.2`. `verdictContext.reasonCodes` и `decisionReasonCodes` содержат причины ограничения: например class_deficits_mismatch, latent_without_deficit, inflammation_without_anemia. inflammation — решение по цели inflammation_anemia; общий и активный B12 учитываются как альтернативы. Снимок фраз сохраняется с отчётом; последующая правка каталога не меняет существующий результат. Эти фразы требуют медицинского согласования, назначений препаратов/доз нет.

`--phrase-file` заменяет следующие шаги проверяемым локальным JSON; формат в `../backend/README.md`. `POST /api/data-sufficiency` принимает `{inputs,audience}` и позволяет неполные основные входы. `POST /api/recommendations/preview` принимает также `verdict` и возвращает явно обозначенную симуляцию без сохранения; передавать собственный вердикт в predict нельзя. `GET /api/recommendations/catalog?audience=doctor|patient` возвращает каталог и правила.

## Служебные методы и ошибки

- GET /api/health — доступность, версии, OCR, лимиты.
- GET /api/features — 37 признаков, описания, единицы; version — SHA-256 исходного словаря.
- GET /api/models/status — ready/unavailable/disabled, версия, выбранные модели, словарь единиц, порог 0,5 и calibrated=false. При --no-model прежний api-rule отключает модель явно.

400 — неверный multipart/заголовок; 403 — запрещённый Origin; 404 — нет записи; 409 — конфликт ревизии/подтверждения/роли; 413 — размер; 422 — входы/нечитаемый PDF/нет кандидатов; 429 — распознавание занято; 503 — модель недоступна/ошибка вычисления или заданный файл фраз неисправен; 504 — таймаут. Пользовательские ошибки имеют строковый detail; ошибки структуры Pydantic — массив detail. Клиент показывает строку или общий текст ошибки. Ответы Cache-Control: no-store.

## Хранение, CSV/XLSX, экспорт

Медицинские записи/PDF находятся в RAM Store до часа от research-подтверждения, не продлеваются чтением; очистка/logout/DELETE/перезапуск удаляют. Историческая disk DB не читается. Учётные записи/организации/API-хеши остаются в auth.sqlite3. Все объекты закрыты research-cookie и, для doctor, doctorId. Другой grant/user получает 404.

CSV/XLSX читаются в браузере; передаётся выбранная запись. XLSX — первый видимый лист, shared/inline strings, кэшированные формулы; формулы не исполняются. Конвертация единиц CSV/XLSX не выполняется.

«Сохранить PDF» формирует и скачивает настоящий PDF локально (pdf-lib + fontkit, встроенный IBM Plex); «Печать» отдельно вызывает печать текущего результата. В обоих вариантах включены назначение сервиса, результат, рекомендации, врачебные scores, происхождение и версия фраз. Промежуточный HTML не скачивается; серверный метод PDF не нужен. JSON остаётся дополнительным экспортом.

## Версии исследовательских моделей

Дополнительно поддерживается `calibrated_v3`: сохранённые v1 головы с калибровкой по OOF кейса. Кандидат доступен отдельно на 8002. Статус и отчёт указывают `calibrationApplied`, `calibration.status` (`none/partial/all_heads`), методы по задачам/панелям, преобразованные и оставленные исходными головы; `clinicalValidated=false`. Веса v3 частично откалиброваны (10/14), поэтому общий `calibrated=false`; этот флаг означает наличие преобразования у всех голов соответствующего набора/активной панели. `scoreMeaning=partially_calibrated_research_score`. Числа остаются исследовательскими scores, низкое значение не исключает дефицит; отображение в процентах заболевания этим не разрешается. Формат выводов/рекомендаций и отказ при скудном вводе сохраняются. Default v1 не изменён; качество и ограничения описаны в `experiments/calibrated_v3/REPORT.md`.

`--model-bundle` допускает также локальный набор robust_v2. Сервер/клиент используют тот же контракт прогнозов; отчёт и models/status дополнительно указывают `modelFamily` (baseline_v1/robust_v2), версия весов включает соответствующее семейство. `classScoresConditionedOnHb` в отчёте указывает, что оценки 12 классов в этой ветке согласованы с известным статусом Hb и нормализованы. `calibrated: false` сохраняется. По умолчанию работает baseline_v1; кандидат v2 не прошёл все проверки перехода. Отдельный сервер на 8001 обслуживает сайт и API с одного origin; dev-сервер 5173 по-прежнему обращается к 8000.

## Research gate и удаление

GET /api/privacy/status → mode research, policyVersion, realPatientProcessingEnabled false, clinicalUseEnabled false, storage volatile, retentionSeconds 3600, acknowledged/expiresAt/dataKind. POST /api/privacy/acknowledge → {policyVersion,dataKind:"synthetic"|"anonymized",researchOnly:true}; HttpOnly SameSite=Strict cookie до часа. Это не юридическое согласие. Нет поддержанного real_patient режима. X-Hema-Client:1 обязателен для изменяющих запросов с cookie и auth/privacy.

Без grant upload/predict/preview/coverage возвращает 428 до обработки. DELETE /api/privacy/session очищает все объекты текущего grant. DELETE /api/observations/{id} удаляет наблюдение/исходные PDF/связанные отчёты; DELETE /api/reports/{id} удаляет только отчёт. Чужая запись — 404. Врачебный вход сам по себе не заменяет grant; login/logout отзывает прежний grant. JSON до 128 КиБ, заполненный Store — 507. Ошибки схемы не отражают введённые значения.

Канонические массивы deficiencyScores/anemiaScores → {code,label,score}. Legacy deficiencyProbabilities/anemiaProbabilities → {code,label,probability} сохранены для совместимости и имеют тот же research-score смысл. scoreMeaning указывает uncalibrated_model_score/partially_calibrated_research_score/calibrated_research_score; ни один вариант не означает клинически проверенную вероятность. UI/PDF используют decimal 0–1 без процента заболевания. Reports имеют researchOnly true, clinicalUseEnabled false, processingMode research, expiresAt.

Stateless лабораторный endpoint /api/integration/predict и Bearer-ключи: backend/INTEGRATIONS.md. В body нельзя выбрать организацию; reportId/observationId null, stored false. Текущий выпуск только исследовательский.
