"""Fetch untouched NHANES test cohorts for v4 without replacing raw files."""
import hashlib
import io
import json
from pathlib import Path
import urllib.request

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = {
    'nhanes_2007_2008': (2007, ['DEMO_E','CBC_E','BIOPRO_E','CRP_E','FERTIN_E','VIT_B6_E']),
    'nhanes_2013_2014': (2013, ['DEMO_H','CBC_H','BIOPRO_H','VITB12_H']),
}


def run():
    for cycle, (year, names) in COMPONENTS.items():
        folder = ROOT / 'data/external/raw' / cycle
        folder.mkdir(parents=True, exist_ok=True)
        records = []
        for name in names:
            path = folder / f'{name}.xpt'
            url = f'https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/{year}/DataFiles/{path.name}'
            raw = path.read_bytes() if path.exists() else urllib.request.urlopen(url, timeout=60).read()
            if not raw.startswith(b'HEADER RECORD*******LIBRARY HEADER RECORD'):
                raise ValueError(f'{name}: not an XPORT file')
            data = pd.read_sas(io.BytesIO(raw), format='xport')
            if 'SEQN' not in data or not data.SEQN.is_unique:
                raise ValueError(f'{name}: invalid participant key')
            if not path.exists():
                with path.open('xb') as handle:
                    handle.write(raw)
            records.append({'path': str(path.relative_to(ROOT)), 'url': url,
                            'documentation': url.removesuffix('.xpt')+'.htm',
                            'sha256': hashlib.sha256(raw).hexdigest(), 'rows': len(data), 'bytes': len(raw)})
            print(name, len(data), flush=True)
        manifest = folder / 'download_manifest.json'
        payload = json.dumps(records, indent=2)+'\n'
        if manifest.exists() and manifest.read_text() != payload:
            raise ValueError('Existing source manifest differs')
        if not manifest.exists():
            manifest.write_text(payload)


if __name__ == '__main__':
    run()
