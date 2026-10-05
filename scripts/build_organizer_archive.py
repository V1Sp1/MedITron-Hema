"""Create an isolated organizer handoff without modifying the working release."""
from pathlib import Path
import hashlib
import importlib.util
import io
import json
import re
import shutil
import zipfile
import joblib

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'deliverables'
STAGE = OUT/'organizers_release'/'MedITron-Hema'
ARCHIVE = OUT/'MedITron_Hema_organizers_2026-10-05.zip'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not STAGE.exists(), 'Use a fresh staging directory.'
    spec = importlib.util.spec_from_file_location('handoff', ROOT/'scripts/bundle_project.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    excluded = {'AGENTS.md','RELEASE_NOTES.md','DEMO_ACCESS_OPTIONS.md',
                'data/reference/medical_text_corrections_2026-10-04.docx',
                'data/case/case_original_superseded.pdf'}
    source_files = []
    for p in module.files():
        rel = p.relative_to(ROOT)
        if rel.as_posix() in excluded or 'PUBLIC_RELEASE_REVIEW' in p.name:
            continue
        if p.suffix in {'.md','.html','.json','.txt','.py','.js','.mjs','.toml'}:
            text = p.read_text('utf-8')
            if re.search(r'(?:wiki|staff|tracker|a)\.yandex-team\.ru|s3\.mds\.yandex\.net', text):
                continue
        target = STAGE/rel;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p,target)
        if p.suffix in {'.md','.html','.json','.txt'}:
            text=target.read_text('utf-8').replace(str(ROOT),'/path/to/Hema')
            text=text.replace('/Users/alanmalakhov','/path/to/user')
            target.write_text(text,encoding='utf-8')
        source_files.append({'path':rel.as_posix(),'sha256':digest(p)})
    # Preserve the unchanged source reference snapshot before model metadata removal.
    provenance = {'source_date':'2026-10-05','source_files':source_files,'model_metadata_changes':[]}
    model_registry = json.loads((STAGE/'backend/data/model_trust.json').read_text())
    bio_registry = json.loads((STAGE/'backend/data/biochemical_model_trust.json').read_text())
    original_trust = json.loads((ROOT/'backend/data/model_trust.json').read_text())
    original_bio = json.loads((ROOT/'backend/data/biochemical_model_trust.json').read_text())
    for family in ['baseline_v1','robust_v2','calibrated_v3','biochemical_v4']:
        rel=f'experiments/{family}/models/selected.joblib'
        source = ROOT/rel;target=STAGE/rel
        expected=(original_bio['bundles'][rel]['sha256'] if family=='biochemical_v4' else original_trust['sha256'][rel])
        assert digest(source)==expected,'Original model differs from trusted registry'
        b = joblib.load(source)
        learned_key='heads' if family=='biochemical_v4' else 'models'
        buffer=io.BytesIO();joblib.dump(b[learned_key],buffer)
        learned_hash=hashlib.sha256(buffer.getvalue()).hexdigest()
        removed=[]
        for key in ['training_rows','training_patients']:
            if key in b:
                b.pop(key);removed.append(key)
        if 'experiment' in b:
            b['experiment']=f'experiments/{family}';removed.append('absolute experiment path')
        joblib.dump(b,target,compress=3)
        reread=joblib.load(target);buffer=io.BytesIO();joblib.dump(reread[learned_key],buffer)
        assert learned_hash==hashlib.sha256(buffer.getvalue()).hexdigest(),'Learned objects changed'
        current=digest(target)
        if family=='biochemical_v4':bio_registry['bundles'][rel]['sha256']=current
        else:model_registry['sha256'][rel]=current
        provenance['model_metadata_changes'].append({'path':rel,'source_sha256':expected,'release_sha256':current,'removed':removed,'learned_objects_unchanged':True})
    for file,registry in [('model_trust.json',model_registry),('biochemical_model_trust.json',bio_registry)]:
        (STAGE/'backend/data'/file).write_text(json.dumps(registry,ensure_ascii=False,indent=2)+'\n')
    # Evidence/gates are unchanged; runtime trust is updated only for organizer copies.
    (STAGE/'RELEASE_PROVENANCE.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n')
    for name in ['hema_patient_cursor.gif','hema_doctor_cursor.gif','README_CURSOR.md']:
        dest=STAGE/'presentation'/name;dest.parent.mkdir(exist_ok=True)
        shutil.copy2(OUT/'presentation'/name,dest)
    deck=OUT/'algorithm_presentation_overview/Hema_overview_final.pptx'
    if deck.exists():shutil.copy2(deck,STAGE/'presentation/Hema_overview_final.pptx')
    (STAGE/'README_ORGANIZERS.md').write_text('''# MedITron — Hema by NeuroNiXxx

Рабочий исследовательский прототип интерпретации анализов по кейсу Сеченовского университета.

1. Распакуйте всю папку MedITron-Hema.
2. Откройте START_HERE.html: в нём документация и инструкции для macOS/Windows.
3. Для быстрого обзора откройте presentation/: презентация и две GIF работы сайта.
4. Для запуска нужны Python 3.12 и первоначальная установка библиотек по docs/06_LOCAL_SETUP.md.
5. После запуска откройте http://127.0.0.1:8000/. Пациентский режим не требует аккаунта.
6. Для врача создайте собственную демонстрационную запись по backend/AUTH.md.

Готовые веса baseline_v1 и отдельного ферритина v4 включены; переобучение не требуется.
Также включены предыдущие исследовательские v2/v3, отчёты экспериментов, тесты и вымышленные примеры PDF/CSV.
Код лабораторного API включён; подробности — backend/INTEGRATIONS.md и docs/11_LABORATORY_API.md.

Это исследовательская демонстрация на вымышленных или действительно обезличенных данных.
Клиническая достоверность не установлена; scores не являются проверенными вероятностями заболеваний.
Не использовать для диагностики, назначения лечения или исключения болезни.

Исходные медицинские таблицы, рабочие аккаунты/ключи/базы, окружение .venv и внутренние рабочие обсуждения не включены.
Служебные списки идентификаторов обучения и абсолютные пути удалены только из копий моделей.
Обученные объекты сохранены без изменений; SHA-256 разрешённых копий обновлены в локальном реестре доверия этой сборки.
RELEASE_PROVENANCE.json сохраняет исходные/новые хеши; исторические отчёты экспериментов относятся к исходным весам.
Полный повтор обучения требует отдельного получения исходных данных; архив предназначен для запуска готовой модели.
''',encoding='utf-8')
    original_readme=(STAGE/'README.md').read_text()
    (STAGE/'README.md').write_text('# MedITron — Hema\n\nНачните с [README_ORGANIZERS.md](README_ORGANIZERS.md).\n\n'+original_readme,encoding='utf-8')
    manifest_files=[]
    for p in sorted(STAGE.rglob('*')):
        if not p.is_file():continue
        assert p.suffix.lower() not in {'.sqlite','.sqlite3','.db','.key','.pem','.p12','.pfx','.log'}
        if p.suffix in {'.py','.js','.mjs','.md','.html','.json','.txt','.toml'}:
            assert not re.search(r'BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|hema_lab_[A-Za-z0-9_-]{40,}|ghp_[A-Za-z0-9]{36}|sk-proj-[A-Za-z0-9_-]{30,}',p.read_text()),p.name
        manifest_files.append({'path':p.relative_to(STAGE).as_posix(),'size_bytes':p.stat().st_size,'sha256':digest(p)})
    (STAGE/'FILES_MANIFEST.json').write_text(json.dumps({'snapshot_date':'2026-10-05','root_in_zip':'MedITron-Hema','files':manifest_files},ensure_ascii=False,indent=2)+'\n')
    # Stable isolated staging is verified before the final ZIP is emitted.
    print(json.dumps({'stage':str(STAGE),'files':len(manifest_files)+1,'stage_ready':True}),flush=True)


if __name__=='__main__':main()
