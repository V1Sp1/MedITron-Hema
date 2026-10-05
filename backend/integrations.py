"""Local administration of research-only organization integration keys."""
import argparse
import os
from pathlib import Path
from .auth import AuthStore, API_SCOPES
from .features import ROOT


def main():
    parser = argparse.ArgumentParser(description='Организации и ключи исследовательского API Hema; реальные пациенты пока не допускаются')
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('HEMA_DATA_DIR', ROOT/'data/local')))
    commands = parser.add_subparsers(dest='command', required=True)
    organization = commands.add_parser('organization-create')
    organization.add_argument('id'); organization.add_argument('--name', required=True); organization.add_argument('--contact', required=True)
    key = commands.add_parser('key-create')
    key.add_argument('organization'); key.add_argument('--label', required=True); key.add_argument('--days', type=int, default=30)
    key.add_argument('--scope', action='append', choices=sorted(API_SCOPES), help='Повторяемый параметр прав. По умолчанию только predict.')
    key.add_argument('--token-file', type=Path, required=True, help='Новый приватный файл; существующий не перезаписывается')
    commands.add_parser('key-list')
    commands.add_parser('key-revoke').add_argument('id')
    args = parser.parse_args()
    store = AuthStore(args.data_dir)
    try:
        if args.command == 'organization-create':
            store.create_organization(args.id, args.name, args.contact)
            print('Организация создана для исследовательского режима. Это не разрешение на медицинскую обработку.')
        elif args.command == 'key-create':
            # Open with exclusive creation before issuing the credential.
            fd = os.open(args.token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            identifier = None
            try:
                identifier, token = store.create_api_key(args.organization, args.label, lifetime_days=args.days, scopes=args.scope)
                with os.fdopen(fd, 'w', encoding='ascii') as output:
                    fd = None
                    output.write(token + '\n')
            except Exception:
                if identifier:
                    store.revoke_api_key(identifier)
                if fd is not None:
                    os.close(fd)
                    fd = None
                args.token_file.unlink(missing_ok=True)
                raise
            finally:
                if fd is not None:
                    os.close(fd)
            print('Ключ сохранён в приватный файл. ID для отзыва: ' + identifier)
        elif args.command == 'key-revoke':
            store.revoke_api_key(args.id); print('Ключ отозван.')
        else:
            for row in store.list_api_keys():
                print(f"{row['id']}\t{row['organization_id']}\t{row['label']}\t{'активен' if row['active'] else 'отозван'}\t{row['scopes']}")
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc)+'\n')
    finally:
        store.close()


if __name__ == '__main__':
    main()
