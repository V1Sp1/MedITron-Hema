# Структура и архитектура проекта Hema

Это карта фактической реализации для нового участника команды. Она связывает пользовательские действия с файлами кода, объясняет границы компонентов и содержит полный реестр файлов передаваемого снимка. Актуальный источник принятых решений — `RELEASE_NOTES.md`; результаты первой серии обучения — `experiments/baseline_v1/REPORT.md`.

## Общая архитектура

Hema — один локальный Python-сервис и статический веб-интерфейс. Браузер отображает HTML/CSS/JavaScript; FastAPI отдаёт страницы и обслуживает API; Python-парсер извлекает PDF; сохранённые классификаторы выполняют inference; SQLite :memory: хранит временные наблюдения/отчёты/PDF; auth.sqlite3 отдельно хранит учётные записи/организации/хеши. В основном способе запуска сайт и API находятся на одном адресе `http://127.0.0.1:8000/`. Отдельный Node.js-сервер не нужен.

```text
Человек → браузер → frontend/src/app.js
                       ↓ frontend/src/api.js
             FastAPI backend/app.py
                  ┌────┴──────────────────────────┐
          PDF-импорт                         ручные/табличные входы
                  ↓                               ↓
 backend/parser_service.py                 backend/features.py
     отдельный Python-процесс               проверка 37 признаков
                  ↓                               ↓
        lab_parser/*                     backend/screening.py
   исходные строки и кандидаты             правило пола и Hb
                  ↓                               ↓
 backend/features.py                     backend/model_service.py
   единицы, черновик формы                ml_baselines/predict.py
                  ↓                       выбранные joblib-веса
 подтверждение человеком                          ↓
                  └────────────────────→ backend/verdicts.py
                                          проверка согласованности
                                                   ↓
                                  backend/recommendations.py
                               полнота, выводы и следующие шаги
                                                   ↓
                                        backend/store.py
                                  RAM SQLite :memory:, TTL и снимок фраз
                                                   ↓
                           браузер → экран результата → PDF/JSON
```

Правило Hb, обучение модели и подбор фраз — три разных механизма. Каталоги текстов не обучают модель. Модельные оценки не меняют порог Hb. Парсер не выбирает диагноз: он сохраняет кандидаты значений и их происхождение.

## Верхний уровень

| Каталог или файл | Назначение | Нужен для обычного запуска |
|---|---|---|
| `backend/` | Сервер, модельный адаптер, проверка входов, рекомендации, SQLite | Да |
| `lab_parser/` | PDF-текст, таблицы, локальный OCR, даты, единицы и структура наблюдения | Да для PDF; пакет ставится вместе с сервером |
| `frontend/` | HTML-страницы, CSS, JS, шрифты, изображения, тесты, снимки и сборка | Да |
| `ml_baselines/` | Обучение, preprocessing, исследовательский inference и отчёт | Да, inference импортирует общий код |
| `experiments/baseline_v1/models/selected.joblib` | Основной переносимый комплект обученных классификаторов | Да для модельного режима |
| `experiments/baseline_v1/` | В рабочем проекте веса/разбиения/метрики; в ZIP selected weights и агрегаты | Для воспроизведения и аудита |
| `experiments/robust_v2/`, `experiments/calibrated_v3/` | Исследовательские кандидаты: выбранные веса, агрегированные метрики, отчёты, снимки кода | Альтернативный inference и аудит |
| `experiments/external_validation_v1/` | Внешняя проверка замороженных v1/v2/v3; протокол, сводные результаты и отчёт | Для оценки переносимости; таблицы отдельных людей исключены |
| `experiments/biochemical_v4/` | Отдельные головы низких биомаркеров, выбранные веса, протокол, пороги, агрегаты и отчёт | Ферритин интегрирован отдельным выходом; B12/PLP доступны в CLI |
| `data/feature_dictionary.json` | Схема 48 столбцов, роли, единицы и хеши источников | Да |
| `data/reference/variables.xlsx` | Предоставленный словарь признаков | Для аудита |
| `data/case/` | Исходные материалы отдельно; CSV/PDF требований в публичный выпуск не включены | CSV вне ZIP; для inference не требуется |
| `data/external/` | Внешние данные, обработанные производные, метаданные и пояснения | В ZIP пояснения и технический unit_mapping.json; построчные таблицы исключены |
| `data/local/` | Создаётся при запуске: auth DB, временные файлы; медзаписи только RAM | Не включён; у получателя создаётся заново |
| `scripts/` | Аудиты, подготовка внешних данных, запуск и сборка передачи | Вспомогательные инструменты |
| `tests/` | Python-тесты парсера, API, модели, рекомендаций | Для разработки |
| `output/` | Сохранённые результаты проверки парсера и наборы примеров | История проверок |
| `tmp/` | Публичные PDF-образцы и изображения для проверки | Рабочие примеры; tmp не включён в ZIP |
| `docs/` | Новые руководства Markdown и HTML | Для команды |
| `pyproject.toml` | Установка Python-пакетов, extras server/model/test и CLI | Да |
| `README.md` | Вход в проект и документацию | Для команды |
| `RELEASE_NOTES.md` | Требования, проверенные факты, решения, история и открытые вопросы | Для всех участников |
| `EXTERNAL_DATASETS.md` | Подробный аудит внешних источников и условий использования | Для исследователей |
| `FEATURE_UNITS.md` | Единицы всех признаков и различия внешних источников | Для интеграции данных |
| `KILICARSLAN_ANALYSIS.md` | Аудит правил разметки и производных Kılıçarslan | Для исследований |

## Точки входа и конфигурация

`python -m backend` вызывает `backend/__main__.py`, разбирает параметры и передаёт их в `create_app`. Затем Uvicorn слушает только loopback. `python -m lab_parser` вызывает CLI PDF-парсера. `python -m ml_baselines.train` создаёт новый эксперимент. `python -m ml_baselines.predict` выполняет исследовательский прогноз из JSON. HTML стартует с `frontend/index.html`, далее открывается выбранная страница роли.

Корень проекта определяется по расположению Python-файлов, а не по имени папки пользователя. Зависимости устанавливаются editable, чтобы код и данные читались из распакованного дерева. Не переносите только `backend/`: ему нужны `frontend/`, `lab_parser/`, `ml_baselines/`, словарь и веса.

Новый контур безопасности: backend/privacy.py, worker_limits.py, privacy_admin.py, integrations.py, auth.py; UI gate frontend/src/privacy.js. Юридические шаблоны каждой стороны — docs/legal. Серверные медицинские записи доступны только своей research-cookie, врачебные дополнительно своей учётной записи. Лабораторный API не сохраняет объекты.

Главные параметры сервера: `--port`, `--data-dir`, `--ocr`, `--freshness-policy`, `--phrase-file`, `--model-bundle`, `--no-model`. Переменная `HEMA_DATA_DIR` позволяет перенести рабочее хранилище. Все параметры объяснены в руководстве бэкенда. В текущей версии нет `.env` с секретом или облачного ключа, необходимого для основного запуска.

## Три сценария движения данных

### Ручной ввод и таблица

Пользователь заполняет возраст, пол и Hb, при желании остальные анализы. CSV/XLSX читаются в браузере, выбирается одна строка. JS отбирает только допустимые входы и передаёт числовой объект в API выбранной роли. Сервер повторно проверяет значения, рассчитывает Hb, запускает сохранённые модели, применяет политику воздержания, сохраняет отчёт со снимком фраз. Браузер отдельно запрашивает рекомендации для ID отчёта и показывает результат. Исходная таблица не загружается целиком на сервер в этом сценарии.

### PDF

Пакет PDF отправляется в multipart-поле `files`. После подтверждения research-сеанса сервер создаёт временные приватные копии, запускает worker, минимизирует извлечение и удаляет каталог. Исходные PDF остаются только RAM BLOB до часа. Он возвращает документы, страницы, строки и измерения. API добавляет канонические единицы и черновик формы, но не выбирает повторные измерения и границы `<`/`>`. Человек проверяет форму, указывает возраст/пол и подтверждает. При расчёте передаются ID и ревизия наблюдения; сервер обновляет проверенные входы и создаёт связанный отчёт.

### Демонстрация

«Посмотреть пример» показывает заранее подготовленные числа из `src/demo.js`. Это пример интерфейса, не текущий inference. Изменение формы снимает демо. Для фактической проверки нужно заполнить форму самостоятельно или импортировать файл и нажать расчёт.

## Согласование схем

Всего в CSV 48 столбцов: 1 идентификатор, 37 входов и 10 меток. На вход модели попадают только 37 признаков. Все 10 меток и `patient_id` исключаются, иначе возникла бы утечка ответа. Пропуск остаётся `null` в API, затем NaN в модели. Нельзя превращать отсутствующее измерение в ноль.

Единицы словаря должны совпадать с единицами обучения. В модели хранится SHA-256 исходного словаря `variables.xlsx`; API дополнительно проверяет набор/порядок признаков, цели и классы. Изменение схемы требует согласованного обновления словаря, фронтенда, API, тестов и весов, а не только переименования одной подписи.

## Где менять поведение

| Задача | Начальные файлы | Что проверить после изменения |
|---|---|---|
| Добавить лабораторный признак | словарь, `backend/features.py`, `ml_baselines/core.py`, `frontend/src/data.js` | Совместимость весов и схемы; переобучение может быть необходимо |
| Исправить извлечение PDF | `lab_parser/parsing.py`, `catalog.py`, `data/analytes.json` | Парсер-тесты, исходные значения, происхождение и повторы |
| Добавить пересчёт единиц | `backend/features.py` | Размерности, исходное число, отсутствие неявных догадок |
| Изменить алгоритм обучения | `ml_baselines/core.py`, `train.py` | Новая серия, отдельная оценка; старый experiment не перезаписывать |
| Изменить отказ от причинного вывода | `backend/model_service.py`, `verdicts.py` | Hb/классы/дефициты, sparse, обе роли, рекомендации |
| Изменить фразы | `backend/data/recommendations_*.json`, `recommendations.py` | Условия, версия, обе роли, ограничения и сохранённые снимки |
| Изменить полноту | `backend/data/sufficiency_policy.json`, `data_sufficiency.py` | Группы альтернатив, минимум 5, влияние на отказ и фразы |
| Изменить интерфейс | `frontend/src/app.js`, CSS, HTML | Состояние формы, ошибки API, экспорт, мобильный размер |
| Изменить PDF-отчёт | `frontend/src/pdf-report.js` | Кириллица, переносы, длинные отчёты, лицензии шрифтов |
| Изменить хранение | `backend/store.py`, `app.py` | Ревизии, перезапуск, приватность путей и старые записи |

## Что ещё не реализовано

Нельзя считать текущую архитектуру полноценным медицинским продуктом. Открыты независимая валидация/калибровка, медицинское согласование текстов, сроки актуальности анализов, детальный выбор источников повторных измерений, распознавание сканов на Windows, авторизация, удаление записей и ограничение памяти worker. Внешние данные не включены в обучение baseline_v1; NHANES использован отдельно для v4. Старые отчёты и старые аналитические предложения в контексте не доказывают реализацию соответствующей функции.

## Полный реестр файлов

Следующая таблица формируется из содержимого передачи. Размеры и SHA-256 находятся в `FILES_MANIFEST.json`. Машинные окружения, личная рабочая база и кэши намеренно исключены. Руководства HTML являются копиями Markdown; реестр не перечисляет сам себя повторно при каждом преобразовании.



## Публичный состав MedITron

Внутренний контекст и исходные материалы кейса не опубликованы. Рабочий алгоритм и ограничения — docs/10_HYBRID_MODEL.md и RELEASE_NOTES.md. Реестр ниже относится к публичной копии; машинные проверки и манифест описываются отдельно.

<!-- generated inventory -->

| Путь | Назначение |
|---|---|
| `.gitignore` | Исключения VCS; архив собирается независимо от Git ignore. |
| `EXTERNAL_DATASETS.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `FEATURE_UNITS.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `KILICARSLAN_ANALYSIS.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `MODEL_PUBLICATION.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `README.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `RELEASE_NOTES.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `START_MAC.command` | Удобная точка запуска на macOS через Python 3.12. |
| `START_WINDOWS.bat` | Удобная точка запуска на Windows через launcher py 3.12. |
| `THIRD_PARTY_NOTICES.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `backend/AUTH.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `backend/INTEGRATIONS.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `backend/README.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `backend/RECOMMENDATIONS.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `backend/__init__.py` | Инициализация Python-пакета backend. |
| `backend/__main__.py` | Точка запуска Python-модуля backend. |
| `backend/accounts.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `backend/app.py` | Сборка FastAPI, middleware, схемы запросов, маршруты и orchestration. |
| `backend/auth.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `backend/data/biochemical_model_trust.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `backend/data/model_trust.json` | SHA-256 разрешённых локальных весов до joblib-десериализации. |
| `backend/data/recommendation_preview_examples.json` | Примеры явного preview для фраз без реального inference. |
| `backend/data/recommendations_doctor.json` | 20 черновых фраз врача, условия, приоритеты и sources. |
| `backend/data/recommendations_patient.json` | 20 черновых фраз пациента, условия, приоритеты и sources. |
| `backend/data/sufficiency_policy.json` | Версия и правила минимальной панели/покрытия. |
| `backend/data_sufficiency.py` | Проверка минимума лабораторных значений и покрытия групп. |
| `backend/features.py` | Канонический словарь, валидация, пересчёт единиц и черновик PDF. |
| `backend/ferritin_service.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `backend/integrations.py` | Администрирование организаций и отзываемых исследовательских ключей. |
| `backend/laboratory_api.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `backend/model_service.py` | Проверка weights, readiness, версия и адаптер модельного отчёта. |
| `backend/parse_worker.py` | Файловый протокол worker → lab_parser → result JSON. |
| `backend/parser_service.py` | Отдельный worker PDF, timeout и завершение процессов на двух ОС. |
| `backend/privacy.py` | Исследовательские сеансы, минимизация, очистка и лимиты API. |
| `backend/privacy_admin.py` | Отдельная проверка/подтверждённое удаление исторических копий. |
| `backend/recommendations.py` | Валидация и детерминированный выбор версионированных фраз. |
| `backend/report_files.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `backend/screening.py` | Входы и отдельное правило Hb; во frontend также CSV. |
| `backend/store.py` | Ограниченная SQLite :memory:, TTL, PDF BLOB, ревизии и каскадное удаление. |
| `backend/verdicts.py` | Строгая схема вердикта и согласованность класса, Hb и бинарных решений. |
| `backend/worker_limits.py` | Ограничения ресурсов worker для POSIX/Windows. |
| `data/external/README.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `data/external/processed/kilicarslan/README.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `data/external/processed/nhanes_case_units_v1/README.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `data/external/unit_mapping.json` | Технический справочник пересчёта единиц внешних исследований; без записей пациентов. |
| `data/feature_dictionary.json` | 48 исходных столбцов, 37 входов, единицы, роли и хеши. |
| `data/reference/variables.xlsx` | Предоставленный канонический словарь столбцов и единиц. |
| `docs/00_START_HERE.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/01_PROJECT_STRUCTURE.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/02_MODEL.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/03_BACKEND.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/04_FRONTEND.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/05_RECOMMENDATIONS.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/06_LOCAL_SETUP.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/07_VERIFICATION.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/08_SECURITY_LEGAL_AUDIT_RU.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/09_SECURITY_REMEDIATION.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/10_HYBRID_MODEL.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/11_LABORATORY_API.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/INDEPENDENT_VALIDATION.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/JURY_GUIDE.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/clinical_validation_template.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `docs/legal/HEALTH_DATA_CONSENT_TEMPLATE.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/legal/MEDICAL_RELEASE_GATE.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/legal/OPERATOR_ROLES.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/legal/PRIVACY_POLICY_TEMPLATE.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/legal/PROCESSING_AGREEMENT_TEMPLATE.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/legal/README.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/legal/RESEARCH_TERMS.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/legal/RETENTION_REQUESTS_INCIDENTS.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `docs/media/hema_doctor_demo.gif` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `docs/media/hema_patient_demo.gif` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `docs/research_gate_verification.jpg` | Визуальный ресурс интерфейса/проверок. |
| `docs/research_modal_mobile_verification.jpg` | Визуальный ресурс интерфейса/проверок. |
| `docs/research_modal_verification.jpg` | Визуальный ресурс интерфейса/проверок. |
| `docs/security_audit_probe.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `docs/security_dependency_audit.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `docs/security_dependency_inventory.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `docs/security_runtime_requirements.txt` | Текстовое пояснение, промпт или извлечённый текст PDF. |
| `docs/security_verification.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `examples/biochemical_v4_input.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `examples/full-demo/README.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `examples/full-demo/input_panels.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `examples/full-demo/reports.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `examples/laboratory_api/sample-pdf-reports.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `examples/laboratory_api/sample-reports.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `examples/laboratory_api/structured_request.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `examples/synthetic_panel.csv` | Одна синтетическая строка для проверки реального API. |
| `experiments/backend_hybrid_v1_v4/REPORT.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `experiments/backend_hybrid_v1_v4/verification.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/baseline_v1/REPORT.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `experiments/baseline_v1/development_comparison.png` | Визуальный ресурс интерфейса/проверок. |
| `experiments/baseline_v1/holdout_confusion.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/baseline_v1/holdout_confusion.png` | Визуальный ресурс интерфейса/проверок. |
| `experiments/baseline_v1/holdout_missingness.png` | Визуальный ресурс интерфейса/проверок. |
| `experiments/baseline_v1/holdout_reliability.png` | Визуальный ресурс интерфейса/проверок. |
| `experiments/baseline_v1/manifest.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/baseline_v1/metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/baseline_v1/mixed_metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/baseline_v1/models/selected.joblib` | Готовый комплект выбранных primary/CBC классификаторов. |
| `experiments/baseline_v1/per_class.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/baseline_v1/selection.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/baseline_v1/selection_scores.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/baseline_v1/subgroups.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/baseline_v1/timings.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/REPORT.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `experiments/biochemical_v4/artifact_hashes.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/biochemical_v4/development_metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/external_gates.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/biochemical_v4/external_metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/external_subgroups.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/fold_metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/models/selected.joblib` | Готовый комплект выбранных primary/CBC классификаторов. |
| `experiments/biochemical_v4/protocol.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/biochemical_v4/protocol.sha256` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/release_decision.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/biochemical_v4/runtime_manifest.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/biochemical_v4/runtime_source_snapshot/backend/data/biochemical_model_trust.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/biochemical_v4/runtime_source_snapshot/examples/biochemical_v4_input.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/biochemical_v4/runtime_source_snapshot/ml_baselines/biochemical_predict.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/runtime_source_snapshot/ml_baselines/biochemical_report.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/runtime_source_snapshot/tests/test_biochemical_v4.py` | Автоматические проверки biochemical_v4. |
| `experiments/biochemical_v4/selection.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/biochemical_v4/selection_lock.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/biochemical_v4/sensitivity.png` | Визуальный ресурс интерфейса/проверок. |
| `experiments/biochemical_v4/source_snapshot/ml_baselines/advanced.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/source_snapshot/ml_baselines/biochemical.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/source_snapshot/ml_baselines/calibration.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/source_snapshot/ml_baselines/core.py` | Схема ML, encode, NaN, preprocessing, модели и сценарии пропусков. |
| `experiments/biochemical_v4/source_snapshot/ml_baselines/external_validate.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/source_snapshot/ml_baselines/nhanes_v4.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/source_snapshot/ml_baselines/train_biochemical.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/source_snapshot/scripts/fetch_nhanes_v4.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/source_snapshot/scripts/prepare_nhanes.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/biochemical_v4/test_cohort_manifest.json` | Автоматические проверки cohort_manifest.json. |
| `experiments/biochemical_v4/verification.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/calibrated_v3/REPORT.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `experiments/calibrated_v3/artifact_hashes.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/calibrated_v3/calibration_selection.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/calibrated_v3/comparison.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/deployment_metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/development_metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/external_metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/external_selection.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/calibrated_v3/fold_metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/manifest.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/calibrated_v3/metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/models/selected.joblib` | Готовый комплект выбранных primary/CBC классификаторов. |
| `experiments/calibrated_v3/release_decision.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/calibrated_v3/reliability.png` | Визуальный ресурс интерфейса/проверок. |
| `experiments/calibrated_v3/reliability_bins.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/runtime_manifest.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/calibrated_v3/runtime_source_snapshot/backend/app.py` | Сборка FastAPI, middleware, схемы запросов, маршруты и orchestration. |
| `experiments/calibrated_v3/runtime_source_snapshot/backend/data/model_trust.json` | SHA-256 разрешённых локальных весов до joblib-десериализации. |
| `experiments/calibrated_v3/runtime_source_snapshot/backend/data_sufficiency.py` | Проверка минимума лабораторных значений и покрытия групп. |
| `experiments/calibrated_v3/runtime_source_snapshot/backend/model_service.py` | Проверка weights, readiness, версия и адаптер модельного отчёта. |
| `experiments/calibrated_v3/runtime_source_snapshot/backend/screening.py` | Входы и отдельное правило Hb; во frontend также CSV. |
| `experiments/calibrated_v3/runtime_source_snapshot/backend/verdicts.py` | Строгая схема вердикта и согласованность класса, Hb и бинарных решений. |
| `experiments/calibrated_v3/runtime_source_snapshot/ml_baselines/calibration.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/runtime_source_snapshot/ml_baselines/calibration_report.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/runtime_source_snapshot/ml_baselines/predict.py` | Исследовательский inference по выбранным CBC/primary weights. |
| `experiments/calibrated_v3/runtime_source_snapshot/scripts/prepare_nhanes.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/runtime_source_snapshot/tests/test_calibration.py` | Автоматические проверки calibration. |
| `experiments/calibrated_v3/source_snapshot/__init__.py` | Инициализация Python-пакета source_snapshot. |
| `experiments/calibrated_v3/source_snapshot/advanced.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/source_snapshot/calibrate.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/source_snapshot/calibration.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/source_snapshot/core.py` | Схема ML, encode, NaN, preprocessing, модели и сценарии пропусков. |
| `experiments/calibrated_v3/source_snapshot/improve.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/source_snapshot/improvement_report.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/calibrated_v3/source_snapshot/predict.py` | Исследовательский inference по выбранным CBC/primary weights. |
| `experiments/calibrated_v3/source_snapshot/report.py` | Графики и подробный отчёт эксперимента. |
| `experiments/calibrated_v3/source_snapshot/train.py` | Общие splits/CV, выбор по OOF, development fit, holdout и артефакты. |
| `experiments/calibrated_v3/verification.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/external_validation_v1/REPORT.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `experiments/external_validation_v1/artifact_hashes.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/external_validation_v1/cohort_manifest.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/external_validation_v1/distribution_shift.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/external_validation_v1/external_quality.png` | Визуальный ресурс интерфейса/проверок. |
| `experiments/external_validation_v1/metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/external_validation_v1/protocol.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/external_validation_v1/protocol.sha256` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/external_validation_v1/release_decision.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/external_validation_v1/report_verification_snapshot/ml_baselines/external_validation_report.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/external_validation_v1/report_verification_snapshot/tests/test_external_validation.py` | Автоматические проверки external_validation. |
| `experiments/external_validation_v1/source_snapshot/ml_baselines/advanced.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/external_validation_v1/source_snapshot/ml_baselines/calibration.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/external_validation_v1/source_snapshot/ml_baselines/core.py` | Схема ML, encode, NaN, preprocessing, модели и сценарии пропусков. |
| `experiments/external_validation_v1/source_snapshot/ml_baselines/external_validate.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/external_validation_v1/source_snapshot/scripts/fetch_nhanes_validation.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/external_validation_v1/source_snapshot/scripts/prepare_nhanes.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/external_validation_v1/subgroup_metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/external_validation_v1/verification.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/robust_v2/REPORT.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `experiments/robust_v2/api_policy_metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/robust_v2/artifact_hashes.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/robust_v2/comparison.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/robust_v2/development_metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/robust_v2/manifest.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/robust_v2/metrics.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/robust_v2/missingness_comparison.png` | Визуальный ресурс интерфейса/проверок. |
| `experiments/robust_v2/models/selected.joblib` | Готовый комплект выбранных primary/CBC классификаторов. |
| `experiments/robust_v2/per_class.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/robust_v2/release_decision.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/robust_v2/release_policy.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/robust_v2/selection.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `experiments/robust_v2/source_snapshot/__init__.py` | Инициализация Python-пакета source_snapshot. |
| `experiments/robust_v2/source_snapshot/advanced.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/robust_v2/source_snapshot/core.py` | Схема ML, encode, NaN, preprocessing, модели и сценарии пропусков. |
| `experiments/robust_v2/source_snapshot/improve.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/robust_v2/source_snapshot/improvement_report.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `experiments/robust_v2/source_snapshot/predict.py` | Исследовательский inference по выбранным CBC/primary weights. |
| `experiments/robust_v2/source_snapshot/report.py` | Графики и подробный отчёт эксперимента. |
| `experiments/robust_v2/source_snapshot/train.py` | Общие splits/CV, выбор по OOF, development fit, holdout и артефакты. |
| `frontend/API_CONTRACT.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `frontend/README.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `frontend/assets/report-preview.html` | HTML-страница, отчёт или браузерная проверка. |
| `frontend/doctor.html` | HTML-страница, отчёт или браузерная проверка. |
| `frontend/favicon.svg` | Визуальный ресурс интерфейса/проверок. |
| `frontend/index.html` | HTML-страница, отчёт или браузерная проверка. |
| `frontend/package.json` | ES modules, Node >=20 и scripts dev/build/test. |
| `frontend/patient.html` | HTML-страница, отчёт или браузерная проверка. |
| `frontend/scripts/build.mjs` | Копирование страниц/ресурсов в dist без bundler. |
| `frontend/scripts/check-pdf.mjs` | Создание образцов PDF-отчётов для визуальной проверки. |
| `frontend/scripts/serve.mjs` | Локальный dev HTTP-сервер фронтенда на 5173. |
| `frontend/src/api.js` | HTTP-клиент, API ошибки, проверка ответа и ревизии наблюдений. |
| `frontend/src/app.js` | Состояние страниц, DOM, форма, файлы, результат и взаимодействия. |
| `frontend/src/assets/fonts/IBMPlexMono-Regular.ttf` | Локальный шрифт IBM Plex для UI/PDF, лицензия рядом. |
| `frontend/src/assets/fonts/IBMPlexSans-Regular.ttf` | Локальный шрифт IBM Plex для UI/PDF, лицензия рядом. |
| `frontend/src/assets/fonts/LICENSE.txt` | Лицензия локально включённой зависимости/шрифта; сохранять при передаче. |
| `frontend/src/assets/hero-blood-flow-v2.png` | Визуальный ресурс интерфейса/проверок. |
| `frontend/src/assets/hero-blood-flow-v2.prompt.txt` | Текстовое пояснение, промпт или извлечённый текст PDF. |
| `frontend/src/assets/hero-blood-v1.png` | Визуальный ресурс интерфейса/проверок. |
| `frontend/src/assets/hero-blood-v1.prompt.txt` | Текстовое пояснение, промпт или извлечённый текст PDF. |
| `frontend/src/auth.js` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `frontend/src/config.js` | Режим api/local и адрес API на серверном/dev порту. |
| `frontend/src/data.js` | 35 лабораторных полей и группы интерфейса. |
| `frontend/src/demo-scenarios.js` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `frontend/src/demo.js` | Заранее подготовленный демонстрационный отчёт, не inference. |
| `frontend/src/export.js` | Печать, JSON/blob-скачивание, экранирование и HTML-утилита. |
| `frontend/src/ferritin.js` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `frontend/src/information.js` | Диалоги о проекте, приватности и обработке. |
| `frontend/src/pages.css` | Стили интерфейса, responsive и/или print. |
| `frontend/src/pdf-report.js` | Создание PDF с кириллицей, шрифтами, переносами и страницами. |
| `frontend/src/privacy.js` | Явное подтверждение исследовательского режима до импорта/расчёта. |
| `frontend/src/recommendation-details.js` | Отображение выводов и полноты отдельно от следующих шагов. |
| `frontend/src/report-details.js` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `frontend/src/screening.js` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `frontend/src/styles.css` | Стили интерфейса, responsive и/или print. |
| `frontend/src/units.js` | Канонические единицы для интерфейса. |
| `frontend/src/vendor/JSZIP-LICENSE.md` | Лицензия локально включённой зависимости/шрифта; сохранять при передаче. |
| `frontend/src/vendor/fontkit-LICENSE.txt` | Лицензия локально включённой зависимости/шрифта; сохранять при передаче. |
| `frontend/src/vendor/fontkit-README.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `frontend/src/vendor/fontkit.min.js` | Локальная JavaScript-зависимость; лицензия в том же каталоге. |
| `frontend/src/vendor/jszip.min.js` | Локальная JavaScript-зависимость; лицензия в том же каталоге. |
| `frontend/src/vendor/pdf-lib-LICENSE.md` | Лицензия локально включённой зависимости/шрифта; сохранять при передаче. |
| `frontend/src/vendor/pdf-lib.min.js` | Локальная JavaScript-зависимость; лицензия в том же каталоге. |
| `frontend/src/xlsx.js` | ZIP/XML XLSX, первый видимый лист, кэш формул и лимиты. |
| `frontend/tests/api.test.mjs` | Автоматические проверки api. |
| `frontend/tests/auth.test.mjs` | Автоматические проверки auth. |
| `frontend/tests/backend.test.mjs` | Автоматические проверки backend. |
| `frontend/tests/demo.test.mjs` | Автоматические проверки demo. |
| `frontend/tests/ferritin.test.mjs` | Автоматические проверки ferritin. |
| `frontend/tests/pdf.test.mjs` | Автоматические проверки pdf. |
| `frontend/tests/privacy.test.mjs` | Автоматические проверки privacy. |
| `frontend/tests/recommendations.test.mjs` | Автоматические проверки recommendations. |
| `frontend/tests/screening.test.mjs` | Автоматические проверки screening. |
| `frontend/tests/xlsx.html` | HTML-страница, отчёт или браузерная проверка. |
| `lab_parser/__init__.py` | Инициализация Python-пакета lab_parser. |
| `lab_parser/__main__.py` | Точка запуска Python-модуля lab_parser. |
| `lab_parser/catalog.py` | Нормализация имен/единиц и загрузка словаря aliases. |
| `lab_parser/cli.py` | Аргументы и коды завершения CLI парсера. |
| `lab_parser/data/analytes.json` | Версионированные aliases 35 лабораторных показателей. |
| `lab_parser/extraction.py` | Текст/таблицы pdfplumber и локальный Apple Vision OCR. |
| `lab_parser/macos_ocr.swift` | Swift-мост к Apple Vision для локального OCR страниц. |
| `lab_parser/models.py` | Dataclass-структуры PDF-документов, страниц, измерений, наблюдения. |
| `lab_parser/parsing.py` | Лабораторные строки, числа, comparator, единицы, даты и материал. |
| `lab_parser/pipeline.py` | Пакет PDF как одно наблюдение, контроль повторов и issues. |
| `ml_baselines/README.md` | Пояснение компонента, аналитический отчёт или лицензия. |
| `ml_baselines/__init__.py` | Инициализация Python-пакета ml_baselines. |
| `ml_baselines/advanced.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `ml_baselines/biochemical.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `ml_baselines/biochemical_predict.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `ml_baselines/biochemical_report.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `ml_baselines/calibrate.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `ml_baselines/calibration.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `ml_baselines/calibration_report.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `ml_baselines/core.py` | Схема ML, encode, NaN, preprocessing, модели и сценарии пропусков. |
| `ml_baselines/external_validate.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `ml_baselines/external_validation_report.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `ml_baselines/improve.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `ml_baselines/improvement_report.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `ml_baselines/nhanes_v4.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `ml_baselines/predict.py` | Исследовательский inference по выбранным CBC/primary weights. |
| `ml_baselines/report.py` | Графики и подробный отчёт эксперимента. |
| `ml_baselines/requirements.txt` | Текстовое пояснение, промпт или извлечённый текст PDF. |
| `ml_baselines/train.py` | Общие splits/CV, выбор по OOF, development fit, holdout и артефакты. |
| `ml_baselines/train_biochemical.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `output/pdf/full-examples/B12-patient.pdf` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `output/pdf/full-examples/iron-doctor.pdf` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `output/pdf/test_uploads/01_cbc.pdf` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `output/pdf/test_uploads/02_iron.pdf` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `output/pdf/test_uploads/03_units_bounds_repeat.pdf` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `output/pdf/test_uploads/04_manual.csv` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `output/pdf/test_uploads/README.txt` | Текстовое пояснение, промпт или извлечённый текст PDF. |
| `output/pdf/test_uploads/expected_results.json` | Структурированные метаданные/результат; контекст в пояснениях каталога. |
| `pyproject.toml` | Установка Python, закреплённые extras, CLI и package data. |
| `requirements-runtime.txt` | Снимок закреплённых и проверенных Python-зависимостей. |
| `scripts/audit_external_datasets.py` | Аудит загруженных внешних наборов и источников. |
| `scripts/audit_feature_units.py` | Сопоставление единиц признаков и источников. |
| `scripts/build_cursor_gifs.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `scripts/build_demo_scenarios.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `scripts/build_organizer_archive.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `scripts/build_presentation_gifs.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `scripts/bundle_project.py` | Повторная сборка ZIP, документации HTML и манифеста. |
| `scripts/fetch_nhanes_v4.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `scripts/fetch_nhanes_validation.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `scripts/laboratory_client.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `scripts/launch_local.py` | Первоначальная установка .venv, проверка weights и запуск сервера. |
| `scripts/prepare_kilicarslan.py` | Проверка/подготовка производных Kılıçarslan без изменения original. |
| `scripts/prepare_nhanes.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `scripts/prepare_public_release.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `scripts/verify_handoff.py` | Проверка распакованного ZIP: хеши, реальные модели, PDF и изоляция доступа. |
| `scripts/verify_hybrid.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `tests/auth_helpers.py` | Ресурс проекта; назначение описано в руководстве родительского компонента. |
| `tests/test_auth.py` | Автоматические проверки auth. |
| `tests/test_backend.py` | Автоматические проверки backend. |
| `tests/test_biochemical_v4.py` | Автоматические проверки biochemical_v4. |
| `tests/test_calibration.py` | Автоматические проверки calibration. |
| `tests/test_candidate_model.py` | Автоматические проверки candidate_model. |
| `tests/test_demo_scenarios.py` | Автоматические проверки demo_scenarios. |
| `tests/test_external_validation.py` | Автоматические проверки external_validation. |
| `tests/test_ferritin_backend.py` | Автоматические проверки ferritin_backend. |
| `tests/test_laboratory_api.py` | Автоматические проверки laboratory_api. |
| `tests/test_laboratory_client.py` | Автоматические проверки laboratory_client. |
| `tests/test_ml_baselines.py` | Автоматические проверки ml_baselines. |
| `tests/test_ml_improvements.py` | Автоматические проверки ml_improvements. |
| `tests/test_model_api.py` | Автоматические проверки model_api. |
| `tests/test_parser.py` | Автоматические проверки parser. |
| `tests/test_parser_worker.py` | Автоматические проверки parser_worker. |
| `tests/test_recommendations.py` | Автоматические проверки recommendations. |
| `tests/test_security.py` | Автоматические проверки security. |
