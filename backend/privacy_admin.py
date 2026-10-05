"""Inspect/purge legacy medical copies offline, without reading their contents."""
import argparse
import os
import shutil
from pathlib import Path
from .features import ROOT
from .privacy import cleanup_abandoned


def main():
    parser = argparse.ArgumentParser(description='Старые данные Hema: удаление требует остановки всех серверов и явного подтверждения')
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('HEMA_DATA_DIR', ROOT/'data/local')))
    parser.add_argument('command', choices=['status', 'purge-legacy', 'cleanup-staging'])
    args = parser.parse_args()
    root = args.data_dir.resolve()
    targets = [root/name for name in ('hema.sqlite3', 'hema.sqlite3-wal', 'hema.sqlite3-shm', 'uploads')
               if (root/name).exists() or (root/name).is_symlink()]
    print('Рабочая папка:', root)
    print('Старых объектов верхнего уровня:', len(targets))
    print('Учётные записи и ключи API не удаляются.')
    if args.command == 'cleanup-staging':
        cleanup_abandoned(root); print('Оставлены папки работающих процессов; очищены помеченные папки завершённых.')
    elif args.command == 'purge-legacy':
        print('Остановите ВСЕ серверы, использующие эту папку. Удаление старой базы и PDF необратимо.')
        if input('Для подтверждения введите DELETE LEGACY: ').strip() != 'DELETE LEGACY':
            parser.exit(2, 'Удаление отменено.\n')
        for target in targets:
            if target.is_symlink() or target.is_file():
                target.unlink()
            else:
                shutil.rmtree(target)
        print('Старые копии в указанной папке удалены. Удалите резервные/скачанные копии отдельно.')


if __name__ == '__main__':
    main()
