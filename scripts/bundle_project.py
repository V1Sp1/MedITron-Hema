"""Build a portable source handoff, offline documentation and SHA-256 manifest."""
from pathlib import Path
import hashlib
import html
import json
import os
import re
import zipfile
import argparse

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {'.git', '.venv', '.agents', '.codex', '.aws', '__pycache__', 'node_modules', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'deliverables', '.ast-index'}
SKIP_FILES = {'.DS_Store', 'FILES_MANIFEST.json'}
SOURCE_DIRS = {'backend', 'frontend', 'lab_parser', 'ml_baselines', 'scripts', 'tests', 'docs', 'examples'}
ROOT_FILES = {'README.md', 'AGENTS.md', 'RELEASE_NOTES.md', 'pyproject.toml', 'START_HERE.html',
              'requirements-runtime.txt',
              'START_MAC.command', 'START_WINDOWS.bat', 'EXTERNAL_DATASETS.md', 'FEATURE_UNITS.md',
              'KILICARSLAN_ANALYSIS.md', 'DEMO_ACCESS_OPTIONS.md', '.gitignore'}
SOURCE_EXTENSIONS = {'.py', '.swift', '.js', '.mjs', '.css', '.html', '.md', '.json', '.toml',
                     '.txt', '.svg', '.png', '.jpg', '.ttf', '.woff', '.woff2', '.csv'}
EXPERIMENT_FAMILIES = {'baseline_v1', 'robust_v2', 'calibrated_v3',
                       'external_validation_v1', 'biochemical_v4', 'backend_hybrid_v1_v4'}
AGGREGATE_FILES = {'REPORT.md', 'manifest.json', 'selection.json', 'selection_scores.csv',
                   'metrics.csv', 'per_class.csv', 'mixed_metrics.csv', 'subgroups.csv', 'timings.csv',
                   'holdout_confusion.json', 'development_metrics.csv', 'api_policy_metrics.csv',
                   'comparison.csv', 'release_decision.json', 'release_policy.json', 'artifact_hashes.json',
                   'verification.json', 'runtime_manifest.json', 'protocol.json', 'protocol.sha256',
                   'cohort_manifest.json', 'test_cohort_manifest.json', 'selection_lock.json',
                   'calibration_selection.json', 'external_selection.json', 'external_gates.json',
                   'external_metrics.csv', 'external_subgroups.csv', 'fold_metrics.csv',
                   'deployment_metrics.csv', 'reliability_bins.csv', 'subgroup_metrics.csv',
                   'distribution_shift.csv', 'holdout_missingness.png', 'holdout_confusion.png',
                   'holdout_reliability.png', 'development_comparison.png',
                   'missingness_comparison.png', 'reliability.png', 'external_quality.png', 'sensitivity.png'}
SYNTHETIC_UPLOADS = {'01_cbc.pdf', '02_iron.pdf', '03_units_bounds_repeat.pdf', '04_manual.csv', 'expected_results.json', 'README.txt'}
SYNTHETIC_REPORTS = {'iron-doctor.pdf', 'B12-patient.pdf'}
APPROVED_DATA_FILES = {'data/feature_dictionary.json', 'data/reference/variables.xlsx',
                       'data/reference/medical_text_corrections_2026-10-04.docx',
                       'data/case/case_current.pdf', 'data/case/case_original_superseded.pdf',
                       'data/external/README.md', 'data/external/unit_mapping.json',
                       'data/external/processed/kilicarslan/README.md',
                       'data/external/processed/nhanes_case_units_v1/README.md'}


def permitted(relative):
    """Positive handoff list: unknown directories/data files are excluded."""
    parts = relative.parts
    name = relative.name
    if (name in SKIP_FILES or name.startswith('.env') or relative.suffix.lower() in
            {'.db', '.sqlite', '.sqlite3', '.key', '.pem', '.p12', '.pfx', '.log'}):
        return False
    if len(parts) == 1:
        return name in ROOT_FILES
    if parts[0] in SOURCE_DIRS:
        if any(p in {'dist', 'preview', 'uploads', 'runtime', 'secrets'} for p in parts[1:-1]):
            return False
        return relative.suffix.lower() in SOURCE_EXTENSIONS
    if relative.as_posix() in APPROVED_DATA_FILES:
        return True
    if parts[0] == 'experiments' and len(parts) >= 3 and parts[1] in EXPERIMENT_FAMILIES:
        if len(parts) == 3 and name in AGGREGATE_FILES:
            return True
        if len(parts) == 4 and parts[2] == 'models' and name == 'selected.joblib':
            return True
        return parts[2] in {'figures', 'source_snapshot', 'runtime_source_snapshot', 'report_verification_snapshot'} and relative.suffix in {'.png', '.svg', '.py', '.md', '.json'}
    return len(parts) == 4 and parts[:3] == ('output', 'pdf', 'test_uploads') and name in SYNTHETIC_UPLOADS or (
        len(parts) == 4 and parts[:3] == ('output', 'pdf', 'full-examples') and name in SYNTHETIC_REPORTS)
PURPOSE = {
    'app.py':'Сборка FastAPI, middleware, схемы запросов, маршруты и orchestration.',
    'model_service.py':'Проверка weights, readiness, версия и адаптер модельного отчёта.',
    'verdicts.py':'Строгая схема вердикта и согласованность класса, Hb и бинарных решений.',
    'screening.py':'Входы и отдельное правило Hb; во frontend также CSV.',
    'features.py':'Канонический словарь, валидация, пересчёт единиц и черновик PDF.',
    'store.py':'Ограниченная SQLite :memory:, TTL, PDF BLOB, ревизии и каскадное удаление.',
    'privacy.py':'Исследовательские сеансы, минимизация, очистка и лимиты API.',
    'privacy.js':'Явное подтверждение исследовательского режима до импорта/расчёта.',
    'integrations.py':'Администрирование организаций и отзываемых исследовательских ключей.',
    'privacy_admin.py':'Отдельная проверка/подтверждённое удаление исторических копий.',
    'worker_limits.py':'Ограничения ресурсов worker для POSIX/Windows.',
    'model_trust.json':'SHA-256 разрешённых локальных весов до joblib-десериализации.',
    'requirements-runtime.txt':'Снимок закреплённых и проверенных Python-зависимостей.',
    'unit_mapping.json':'Технический справочник пересчёта единиц внешних исследований; без записей пациентов.',
    'verify_handoff.py':'Проверка распакованного ZIP: хеши, реальные модели, PDF и изоляция доступа.',
    'parser_service.py':'Отдельный worker PDF, timeout и завершение процессов на двух ОС.',
    'parse_worker.py':'Файловый протокол worker → lab_parser → result JSON.',
    'data_sufficiency.py':'Проверка минимума лабораторных значений и покрытия групп.',
    'recommendations.py':'Валидация и детерминированный выбор версионированных фраз.',
    'core.py':'Схема ML, encode, NaN, preprocessing, модели и сценарии пропусков.',
    'train.py':'Общие splits/CV, выбор по OOF, development fit, holdout и артефакты.',
    'predict.py':'Исследовательский inference по выбранным CBC/primary weights.',
    'report.py':'Графики и подробный отчёт эксперимента.',
    'models.py':'Dataclass-структуры PDF-документов, страниц, измерений, наблюдения.',
    'pipeline.py':'Пакет PDF как одно наблюдение, контроль повторов и issues.',
    'extraction.py':'Текст/таблицы pdfplumber и локальный Apple Vision OCR.',
    'parsing.py':'Лабораторные строки, числа, comparator, единицы, даты и материал.',
    'catalog.py':'Нормализация имен/единиц и загрузка словаря aliases.',
    'cli.py':'Аргументы и коды завершения CLI парсера.',
    'macos_ocr.swift':'Swift-мост к Apple Vision для локального OCR страниц.',
    'app.js':'Состояние страниц, DOM, форма, файлы, результат и взаимодействия.',
    'api.js':'HTTP-клиент, API ошибки, проверка ответа и ревизии наблюдений.',
    'config.js':'Режим api/local и адрес API на серверном/dev порту.',
    'data.js':'35 лабораторных полей и группы интерфейса.',
    'units.js':'Канонические единицы для интерфейса.',
    'xlsx.js':'ZIP/XML XLSX, первый видимый лист, кэш формул и лимиты.',
    'export.js':'Печать, JSON/blob-скачивание, экранирование и HTML-утилита.',
    'pdf-report.js':'Создание PDF с кириллицей, шрифтами, переносами и страницами.',
    'information.js':'Диалоги о проекте, приватности и обработке.',
    'demo.js':'Заранее подготовленный демонстрационный отчёт, не inference.',
    'recommendation-details.js':'Отображение выводов и полноты отдельно от следующих шагов.',
    'build.mjs':'Копирование страниц/ресурсов в dist без bundler.',
    'serve.mjs':'Локальный dev HTTP-сервер фронтенда на 5173.',
    'check-pdf.mjs':'Создание образцов PDF-отчётов для визуальной проверки.',
    'launch_local.py':'Первоначальная установка .venv, проверка weights и запуск сервера.',
    'bundle_project.py':'Повторная сборка ZIP, документации HTML и манифеста.',
    'audit_external_datasets.py':'Аудит загруженных внешних наборов и источников.',
    'audit_feature_units.py':'Сопоставление единиц признаков и источников.',
    'prepare_kilicarslan.py':'Проверка/подготовка производных Kılıçarslan без изменения original.',
    'pyproject.toml':'Установка Python, закреплённые extras, CLI и package data.',
    'package.json':'ES modules, Node >=20 и scripts dev/build/test.',
    'feature_dictionary.json':'48 исходных столбцов, 37 входов, единицы, роли и хеши.',
    'sufficiency_policy.json':'Версия и правила минимальной панели/покрытия.',
    'recommendations_patient.json':'20 черновых фраз пациента, условия, приоритеты и sources.',
    'recommendations_doctor.json':'20 черновых фраз врача, условия, приоритеты и sources.',
    'recommendation_preview_examples.json':'Примеры явного preview для фраз без реального inference.',
    'analytes.json':'Версионированные aliases 35 лабораторных показателей.',
    'RELEASE_NOTES.md':'Общие требования, проверенные факты, решения и история.',
    'AGENTS.md':'Правила работы над проектом для агентов.',
    'selected.joblib':'Готовый комплект выбранных primary/CBC классификаторов.',
    'deficiency_anemia.csv':'Неизменённый основной CSV 840 пациентов для воспроизведения.',
    'case_current.pdf':'Актуальные требования кейса Сеченовского университета.',
    'case_original_superseded.pdf':'Историческая версия кейса; не источник текущих чисел.',
    'START_MAC.command':'Удобная точка запуска на macOS через Python 3.12.',
    'START_WINDOWS.bat':'Удобная точка запуска на Windows через launcher py 3.12.',
    'synthetic_panel.csv':'Одна синтетическая строка для проверки реального API.',
    'variables.xlsx':'Предоставленный канонический словарь столбцов и единиц.'
}


def files():
    result=[]
    for folder, dirs, names in os.walk(ROOT):
        if (Path(folder)/'.hema-runtime').exists():
            dirs[:] = []
            continue
        def allowed_folder(d):
            child = Path(folder)/d
            rel = child.relative_to(ROOT)
            if d in SKIP_DIRS or d.endswith('.egg-info') or child.is_symlink() or (child/'.hema-runtime').exists():
                return False
            if rel.parts[0] in SOURCE_DIRS:
                return not any(p in {'dist','preview','uploads','runtime','secrets'} for p in rel.parts)
            return (any(Path(f).is_relative_to(rel) for f in APPROVED_DATA_FILES)
                    or any(Path('experiments/'+family).is_relative_to(rel) or rel.is_relative_to(Path('experiments/'+family))
                           for family in EXPERIMENT_FAMILIES)
                    or any(Path('output/pdf/'+folder).is_relative_to(rel) or rel.is_relative_to(Path('output/pdf/'+folder))
                           for folder in ('test_uploads','full-examples')))
        dirs[:] = sorted(d for d in dirs if allowed_folder(d))
        for name in sorted(names):
            p=Path(folder)/name
            if p.is_symlink() or not permitted(p.relative_to(ROOT)):
                continue
            if p.suffix.lower() in SOURCE_EXTENSIONS:
                text = p.read_text('utf-8', errors='ignore')
                if re.search(r'BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|hema_lab_[A-Za-z0-9_-]{40,}|ghp_[A-Za-z0-9]{36}|sk-proj-[A-Za-z0-9_-]{30,}', text):
                    raise ValueError('В разрешённом файле обнаружен возможный секрет: '+str(p.relative_to(ROOT)))
            result.append(p)
    return sorted(result)


def purpose(path):
    rel=path.relative_to(ROOT).as_posix()
    name=path.name
    if rel.startswith('frontend/dist/'):
        return 'Статическая копия исходного frontend после build; аналогичный исходник в frontend/.'
    if name in PURPOSE:
        return PURPOSE[name]
    if name.startswith('test_') or name.endswith('.test.mjs'):
        return 'Автоматические проверки '+name.replace('test_','').replace('.test.mjs','').replace('.py','')+'.'
    if name=='__main__.py': return 'Точка запуска Python-модуля '+path.parent.name+'.'
    if name=='__init__.py': return 'Инициализация Python-пакета '+path.parent.name+'.'
    if 'LICENSE' in name: return 'Лицензия локально включённой зависимости/шрифта; сохранять при передаче.'
    if rel.startswith('docs/'): return 'Руководство команды; HTML — браузерная версия одноимённого Markdown.'
    if rel.startswith('data/external/raw/') or rel.startswith('data/external/review_only/'):
        return 'Внешний исходник/метаданные для аудита; источник и ограничения в data/external/README.md.'
    if rel.startswith('data/external/processed/'):
        return 'Проверенная производная/аудит Kılıçarslan; описание в README.md этого каталога.'
    if rel.startswith('experiments/baseline_v1/'):
        if path.suffix=='.joblib': return 'Сериализованные fitted-модели конфигурации '+path.stem+' по семи целям.'
        return 'Сохранённый результат baseline_v1: '+path.stem+'; методика/столбцы в REPORT.md и docs/02_MODEL.md.'
    if rel.startswith('frontend/preview/'): return 'Снимок интерфейса/экспорта из проверок; исторический пример, не динамический результат.'
    if rel.startswith('tmp/'): return 'Публичный PDF-образец или изображение для проверки парсера; не требуется для сервера.'
    if rel.startswith('output/'): return 'Сохранённый результат разбора/пакет тестовых файлов; происхождение в README образцов.'
    if path.suffix=='.md': return 'Пояснение компонента, аналитический отчёт или лицензия.'
    if path.suffix=='.css': return 'Стили интерфейса, responsive и/или print.'
    if path.suffix=='.html': return 'HTML-страница, отчёт или браузерная проверка.'
    if path.suffix=='.ttf': return 'Локальный шрифт IBM Plex для UI/PDF, лицензия рядом.'
    if path.suffix in {'.png','.jpg','.svg'}: return 'Визуальный ресурс интерфейса/проверок.'
    if path.suffix=='.txt': return 'Текстовое пояснение, промпт или извлечённый текст PDF.'
    if 'vendor/' in rel: return 'Локальная JavaScript-зависимость; лицензия в том же каталоге.'
    if path.suffix=='.json': return 'Структурированные метаданные/результат; контекст в пояснениях каталога.'
    if path.name=='.gitignore': return 'Исключения VCS; архив собирается независимо от Git ignore.'
    return 'Ресурс проекта; назначение описано в руководстве родительского компонента.'


def inline(text):
    text=html.escape(text)
    code=[]
    def stash(m):
        code.append('<code>'+m.group(1)+'</code>'); return '\x00'+str(len(code)-1)+'\x00'
    text=re.sub(r'`([^`]+)`',stash,text)
    def link(m):
        url=html.unescape(m.group(2))
        candidate = (ROOT/'docs'/url).resolve()
        if url.endswith('.md') and candidate.exists() and candidate.is_relative_to(ROOT/'docs'):
            url=url[:-3]+'.html'
        return '<a href="'+html.escape(url,quote=True)+'">'+m.group(1)+'</a>'
    text=re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', lambda m: '<img alt="'+html.escape(html.unescape(m.group(1)),quote=True)+'" src="'+html.escape(html.unescape(m.group(2)),quote=True)+'">', text)
    text=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',link,text)
    text=re.sub(r'\*\*([^*]+)\*\*',r'<strong>\1</strong>',text)
    return re.sub(r'\x00(\d+)\x00',lambda m:code[int(m.group(1))],text)


def render(markdown):
    lines=markdown.splitlines(); out=[]; i=0
    while i<len(lines):
        line=lines[i]
        if not line.strip(): i+=1; continue
        if line.startswith('```'):
            language=line[3:]; content=[]; i+=1
            while i<len(lines) and not lines[i].startswith('```'): content.append(lines[i]); i+=1
            out.append('<pre><code>'+html.escape('\n'.join(content))+'</code></pre>'); i+=1; continue
        if line.startswith('#'):
            match=re.match(r'^(#{1,6})\s+(.*)',line)
            if match:
                level=len(match[1]); label=match[2]
                out.append(f'<h{level}>{inline(label)}</h{level}>'); i+=1; continue
        if line.startswith('|') and i+1<len(lines) and re.match(r'^\|[\s:|\-]+\|$',lines[i+1]):
            rows=[line]; i+=2
            while i<len(lines) and lines[i].startswith('|'): rows.append(lines[i]); i+=1
            cells=lambda row: [x.strip() for x in row.strip().strip('|').split('|')]
            out.append('<div class="table"><table><thead><tr>'+''.join('<th>'+inline(x)+'</th>' for x in cells(rows[0]))+'</tr></thead><tbody>')
            for row in rows[1:]: out.append('<tr>'+''.join('<td>'+inline(x)+'</td>' for x in cells(row))+'</tr>')
            out.append('</tbody></table></div>'); continue
        match=re.match(r'^(\d+\.|[-*])\s+(.*)',line)
        if match:
            tag='ol' if match[1][0].isdigit() else 'ul'; out.append('<'+tag+'>')
            while i<len(lines):
                m=re.match(r'^(\d+\.|[-*])\s+(.*)',lines[i])
                if not m: break
                out.append('<li>'+inline(m[2])+'</li>'); i+=1
            out.append('</'+tag+'>'); continue
        paragraph=[line]; i+=1
        while i<len(lines) and lines[i].strip() and not re.match(r'^(#|```|\||\d+\. |[-*] )',lines[i]): paragraph.append(lines[i]); i+=1
        out.append('<p>'+inline(' '.join(paragraph))+'</p>')
    return '\n'.join(out)


STYLE='''body{margin:0;background:#eef4f5;color:#103648;font:17px/1.65 system-ui,sans-serif}main{max-width:1050px;margin:24px auto;background:white;padding:38px;border-radius:12px}h1{font-size:32px;line-height:1.25}h2{margin-top:42px}img{max-width:100%;height:auto}a{color:#086a70}code{font:14px/1.5 ui-monospace,monospace;background:#edf3f4;padding:2px 4px}pre{overflow:auto;background:#edf3f4;padding:18px;border-radius:6px;white-space:pre}pre code{padding:0}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:9px;text-align:left;border:1px solid #d4e0e3;vertical-align:top}th{background:#eef5f6}.table{overflow:auto}nav{display:flex;flex-wrap:wrap;gap:12px;border-bottom:1px solid #d4e0e3;padding-bottom:18px}p,li{overflow-wrap:anywhere}@media(max-width:650px){main{margin:0;padding:20px;border-radius:0}h1{font-size:26px}}@media print{body{background:white}main{margin:0;padding:0;max-width:none}nav{display:none}pre{white-space:pre-wrap}.table{overflow:visible}h2,h3{break-after:avoid}tr{break-inside:avoid}}'''


def document(title,body,navigation):
    return '<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>'+STYLE+'</style></head><body><main><nav>'+navigation+'</nav>'+body+'</main></body></html>'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-name', default='Hema_project_2026-10-05_research_secure.zip')
    args = parser.parse_args()
    if Path(args.archive_name).name != args.archive_name or not args.archive_name.endswith('.zip'):
        parser.error('archive-name must be a ZIP filename without a directory')
    structure=ROOT/'docs/01_PROJECT_STRUCTURE.md'
    text=structure.read_text('utf-8').split('<!-- generated inventory -->')[0].rstrip()
    inventory=files()
    text+='\n\n<!-- generated inventory -->\n\n| Путь | Назначение |\n|---|---|\n'
    for p in inventory:
        text+=f'| `{p.relative_to(ROOT).as_posix()}` | {purpose(p)} |\n'
    structure.write_text(text+'\n',encoding='utf-8')
    docs=sorted((ROOT/'docs').glob('*.md'))
    nav='<a href="../START_HERE.html">Начало и все руководства</a>'
    for p in docs:
        content=p.read_text('utf-8'); title=content.splitlines()[0].lstrip('# ')
        p.with_suffix('.html').write_text(document(title,render(content),nav+'<a href="'+p.name+'">Текстовый исходник</a>'),encoding='utf-8')
    for p in sorted((ROOT/'docs/legal').glob('*.md')):
        content=p.read_text('utf-8'); title=content.splitlines()[0].lstrip('# ')
        p.with_suffix('.html').write_text(document(title,render(content).replace('.md"','.html"'),'<a href="../09_SECURITY_REMEDIATION.html">Исправления и ограничения</a><a href="README.html">Юридическая подготовка</a>'),encoding='utf-8')
    links='<h1>Hema документация и запуск</h1><p>Передача проекта от 5 октября 2026 года. Начните с инструкции вашей ОС. Все руководства открываются локально; интернет для чтения не нужен.</p><p><strong>Только исследовательский режим: синтетические или действительно обезличенные данные. Реальные идентифицируемые пациенты и клиническое применение пока не допускаются.</strong></p><ul>'
    for p in docs:
        title=p.read_text('utf-8').splitlines()[0].lstrip('# ')
        links+='<li><a href="docs/'+p.stem+'.html">'+html.escape(title)+'</a></li>'
    links+='</ul><p><a href="docs/legal/README.html">Шаблоны документов каждой клиники/лаборатории</a></p><p>Для запуска: START_MAC.command или START_WINDOWS.bat. Сначала установите Python 3.12 по инструкции. Обученные веса включены, Node.js для основного запуска не требуется.</p><p>Новые медицинские записи находятся в памяти до часа и удаляются при очистке/перезапуске. Учётные записи сохраняются отдельно. Рабочие базы, ключи и исходные медицинские таблицы исключены из ZIP; код и пояснения включены. На Windows OCR сканов недоступен.</p>'
    (ROOT/'START_HERE.html').write_text(document('Hema документация и запуск',links,'<a href="docs/06_LOCAL_SETUP.html">Пошаговая установка</a>'),encoding='utf-8')
    inventory=files()
    manifest={'snapshot_date':'2026-10-05','root_in_zip':'Hema','exclusions':['all runtime roots/databases/keys/logs','unapproved directories','raw clinical datasets and external downloads','row-level experiment artifacts','.venv/','.git/','caches','symlinks','deliverables/'],
              'note':'Research-only source handoff. Only approved synthetic PDF fixtures and aggregate experiment outputs. Raw datasets require separately verified rights and are not copied. Manifest itself does not hash itself.',
              'files':[{'path':p.relative_to(ROOT).as_posix(),'size_bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in inventory]}
    (ROOT/'FILES_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    out=ROOT/'deliverables'; out.mkdir(exist_ok=True)
    archive=out/args.archive_name
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in inventory+[ROOT/'FILES_MANIFEST.json']:
            z.write(p,'Hema/'+p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for item in manifest['files']:
            assert hashlib.sha256(z.read('Hema/'+item['path'])).hexdigest()==item['sha256']
        assert not any('/data/local/' in n or '/.venv/' in n or '/.git/' in n for n in z.namelist())
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(digest+'  '+archive.name+'\n',encoding='ascii')
    print(json.dumps({'archive':str(archive),'files':len(inventory)+1,'bytes':archive.stat().st_size,'sha256':digest},ensure_ascii=False))


if __name__=='__main__': main()
