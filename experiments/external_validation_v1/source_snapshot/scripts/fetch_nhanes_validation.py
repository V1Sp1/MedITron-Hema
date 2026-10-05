"""Fetch official NHANES 2005–2006 components without replacing existing raw files."""
import hashlib
import io
import json
from pathlib import Path
import urllib.request

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ['DEMO_D', 'CBC_D', 'BIOPRO_D', 'FERTIN_D', 'TFR_D',
              'FETIB_D', 'B12_D', 'FOLATE_D', 'HCY_D', 'VIT_B6_D', 'CRP_D']
BASE_URL = 'https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2005/DataFiles/'


def run():
    folder = ROOT / 'data/external/raw/nhanes_2005_2006'
    folder.mkdir(parents=True, exist_ok=True)
    records = []
    for name in COMPONENTS:
        path = folder / f'{name}.xpt'
        url = BASE_URL + path.name
        raw = path.read_bytes() if path.exists() else urllib.request.urlopen(url, timeout=60).read()
        if not raw.startswith(b'HEADER RECORD*******LIBRARY HEADER RECORD'):
            raise ValueError(f'{name}: not an XPORT file')
        table = pd.read_sas(io.BytesIO(raw), format='xport')
        if 'SEQN' not in table or not table.SEQN.is_unique:
            raise ValueError(f'{name}: invalid participant key')
        if not path.exists():
            with path.open('xb') as handle:
                handle.write(raw)
        records.append({'path': str(path.relative_to(ROOT)), 'url': url,
                        'documentation': BASE_URL + name + '.htm',
                        'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'rows': len(table)})
        print(name, len(table), flush=True)
    output = folder / 'download_manifest.json'
    payload = json.dumps(records, indent=2) + '\n'
    if output.exists() and output.read_text() != payload:
        raise ValueError('Existing source manifest differs')
    if not output.exists():
        output.write_text(payload)


if __name__ == '__main__':
    run()
