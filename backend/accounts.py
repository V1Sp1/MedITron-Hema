"""Local account administration; passwords never appear in command arguments."""
import argparse
import getpass
import os
from pathlib import Path
from .auth import AuthStore
from .features import ROOT


def main():
    parser = argparse.ArgumentParser(description='Учётные записи врачей Hema; выполняет администратор организации')
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('HEMA_DATA_DIR', ROOT / 'data/local')))
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    create = commands.add_parser('create'); create.add_argument('username'); create.add_argument('--name', default='')
    create.add_argument('--organization', default='local-research')
    for name in ('reset-password', 'disable', 'enable'):
        commands.add_parser(name).add_argument('username')
    args = parser.parse_args()
    store = AuthStore(args.data_dir)
    try:
        if args.command == 'list':
            for user in store.list_users():
                print(f"{user['username']}\t{'активна' if user['active'] else 'отключена'}\t{user['displayName']}")
        elif args.command in ('create', 'reset-password'):
            password = getpass.getpass('Новый пароль (15–128 символов): ')
            if password != getpass.getpass('Повторите пароль: '):
                raise ValueError('Пароли не совпадают.')
            if args.command == 'create':
                store.create_user(args.username, password, args.name, args.organization)
            else:
                store.change_user(args.username, password=password)
            print('Учётная запись обновлена. Передайте данные для входа пользователю отдельно.')
        else:
            store.change_user(args.username, active=args.command == 'enable')
            print('Статус обновлён; прежние сеансы завершены.')
    except ValueError as exc:
        parser.exit(2, str(exc)+'\n')
    finally:
        store.close()


if __name__ == '__main__':
    main()
