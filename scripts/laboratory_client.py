"""Reference laboratory client: private output files, verified TLS, no redirects.
Requires: pip install '.[integration]'. Only synthetic/anonymized research inputs.
"""
import argparse
import ipaddress
import json
import os
import ssl
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class LaboratoryError(RuntimeError):
    pass


def private_output(path, content):
    """Do not overwrite existing reports or expose their content in stdout."""
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as target:
            target.write(content)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


class LaboratoryClient:
    def __init__(self, base_url, token_file, *, ssl_context=None, transport=None):
        url = urlsplit(base_url)
        if (url.scheme not in {'http', 'https'} or not url.hostname or url.username or
                url.password or url.query or url.fragment or url.path not in {'', '/'}):
            raise ValueError('Base URL must contain only scheme, host and port.')
        try:
            local = ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            local = url.hostname == 'localhost'
        if url.scheme != 'https' and not local:
            raise ValueError('External laboratories require HTTPS.')
        path = Path(token_file)
        if os.name == 'posix' and path.stat().st_mode & 0o077:
            raise ValueError('API key file must be private (chmod 600).')
        if path.stat().st_size > 256:
            raise ValueError('Invalid API key file.')
        token = path.read_text('ascii').strip()
        if not token or len(token) > 128 or any(ch.isspace() for ch in token):
            raise ValueError('Invalid API key file.')
        self.http = httpx.Client(base_url=base_url.rstrip('/'),
            headers={'Authorization': 'Bearer '+token},
            verify=ssl_context or ssl.create_default_context(), transport=transport,
            timeout=httpx.Timeout(240, connect=10), follow_redirects=False, trust_env=False)

    def close(self):
        self.http.close()

    def __enter__(self):return self
    def __exit__(self, *args):self.close()

    def _request(self, method, suffix, **kwargs):
        response = self.http.request(method, '/api/integration/v1'+suffix, **kwargs)
        if not 200 <= response.status_code < 300:
            # Avoid reflecting medical values, filenames, HTML or secrets from a gateway.
            retry = response.headers.get('retry-after')
            detail = f'Laboratory API returned HTTP {response.status_code}.'
            if retry and retry.isdigit():detail += f' Retry after {retry} seconds.'
            raise LaboratoryError(detail)
        return response

    def capabilities(self):return self._request('GET', '/capabilities').json()

    def research_metadata(self, data_kind='synthetic'):
        if data_kind not in {'synthetic', 'anonymized'}:
            raise ValueError('Only synthetic or genuinely anonymized research data are allowed.')
        return {'policyVersion':self.capabilities()['policyVersion'],
                'dataKind':data_kind, 'researchOnly':True}

    def reports(self, request):
        """Return attachment bytes; request includes research acknowledgment and inputs."""
        return self._request('POST', '/reports', json=request)

    def upload_pdfs(self, paths, metadata):
        if not 1 <= len(paths) <= 10:
            raise ValueError('Upload from 1 to 10 PDFs.')
        with ExitStack() as files:
            upload = []
            for index, path in enumerate(paths):
                path = Path(path)
                if path.stat().st_size > 20*1024*1024:
                    raise ValueError('Each PDF must be at most 20 MiB.')
                upload.append(('files', (f'analysis-{index+1}.pdf', files.enter_context(path.open('rb')), 'application/pdf')))
            return self._request('POST', '/observations/pdf',
                data={'metadata':json.dumps(metadata)}, files=upload).json()

    @staticmethod
    def _identifier(value):
        from uuid import UUID
        return str(UUID(value))

    def draft(self, identifier):
        return self._request('GET', '/observations/'+self._identifier(identifier)).json()

    def reviewed_reports(self, draft, request, *, reviewed):
        if reviewed is not True:
            raise ValueError('Review normalized values, units, dates and missing data first.')
        return self._request('POST', '/observations/'+self._identifier(draft['observationId'])+'/reports',
            json={**request, 'revision':draft['revision'], 'inputs':draft['inputs'], 'reviewedObservation':True})

    def delete_draft(self, identifier):
        return self._request('DELETE', '/observations/'+self._identifier(identifier)).json()


def main():
    parser = argparse.ArgumentParser(description='Hema laboratory research API; no real-patient clinical processing')
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--ca-file', type=Path)
    parser.add_argument('--client-cert', type=Path)
    parser.add_argument('--client-key', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    structured = commands.add_parser('report')
    structured.add_argument('--request', type=Path, required=True)
    structured.add_argument('--output', type=Path, required=True)
    upload = commands.add_parser('upload-pdf')
    upload.add_argument('pdfs', type=Path, nargs='+')
    upload.add_argument('--age', type=int, required=True)
    upload.add_argument('--sex', choices=['F','M'], required=True)
    upload.add_argument('--data-kind', choices=['synthetic','anonymized'], default='synthetic')
    upload.add_argument('--output', type=Path, required=True)
    review = commands.add_parser('pdf-report')
    review.add_argument('--draft', type=Path, required=True, help='Normalized JSON, checked and corrected by a human')
    review.add_argument('--reviewed', action='store_true', required=True)
    review.add_argument('--data-kind', choices=['synthetic','anonymized'], default='synthetic')
    review.add_argument('--audience', choices=['patient','doctor','both'], default='both')
    review.add_argument('--format', choices=['pdf','json','zip'], default='zip')
    review.add_argument('--pregnancy-status', choices=['unknown','not_pregnant','pregnant'], default='unknown')
    review.add_argument('--output', type=Path, required=True)
    deletion = commands.add_parser('delete-draft')
    deletion.add_argument('identifier')
    commands.add_parser('capabilities')
    args = parser.parse_args()
    try:
        context = ssl.create_default_context(cafile=str(args.ca_file) if args.ca_file else None)
        if bool(args.client_cert) != bool(args.client_key):
            raise ValueError('Client certificate and key must be supplied together.')
        if args.client_cert:context.load_cert_chain(str(args.client_cert), str(args.client_key))
        with LaboratoryClient(args.url, args.token_file, ssl_context=context) as client:
            if args.command == 'capabilities':
                print(json.dumps(client.capabilities(), ensure_ascii=False, indent=2));return
            if args.command == 'delete-draft':
                client.delete_draft(args.identifier);print('Draft deleted.');return
            if args.output.exists():raise FileExistsError('Choose a new output file.')
            if args.command == 'report':
                response = client.reports(json.loads(args.request.read_text('utf-8')))
                content = response.content
            elif args.command == 'upload-pdf':
                metadata = {**client.research_metadata(args.data_kind), 'age_years':args.age, 'sex':args.sex}
                result = client.upload_pdfs(args.pdfs, metadata)
                content = json.dumps(result, ensure_ascii=False, indent=2).encode()
            else:
                draft = json.loads(args.draft.read_text('utf-8'))
                request = {**client.research_metadata(args.data_kind), 'audience':args.audience,
                           'format':args.format, 'pregnancyStatus':args.pregnancy_status}
                content = client.reviewed_reports(draft, request, reviewed=args.reviewed).content
            private_output(args.output, content)
            print('Result saved to a new private file.')
            if args.command == 'upload-pdf':
                print('Review the draft before calling pdf-report. It expires in at most 15 minutes.')
    except (ValueError, OSError, KeyError, LaboratoryError, httpx.HTTPError):
        # CLI prints no request/response body, certificate secrets or medical data.
        parser.exit(2, 'Operation failed. Check the request, API permissions, TLS and output path.\n')


if __name__ == '__main__':main()
