"""Local inference for the separately trusted research biochemical v4 bundle."""
import argparse
import hashlib
import io
import json
from pathlib import Path

import joblib
import numpy as np

from .biochemical import ENDPOINTS,BiochemicalClassifier,predict_biochemical
from .core import ROOT,FEATURES
from .external_validate import sha

DEFAULT_BUNDLE = ROOT/'experiments/biochemical_v4/models/selected.joblib'
TRUST = ROOT/'backend/data/biochemical_model_trust.json'


def load_candidate(path=DEFAULT_BUNDLE,trust_path=TRUST):
    path = Path(path).resolve()
    registry = json.loads(Path(trust_path).read_text())
    entries = {str((ROOT/name).resolve()):entry for name,entry in registry['bundles'].items()}
    if str(path) not in entries:
        raise ValueError('Biochemical bundle is not explicitly trusted')
    entry = entries[str(path)]
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError('Biochemical weight hash differs before deserialization')
    gates_path = path.parent.parent/'external_gates.json'
    if sha(gates_path) != entry['external_gates_sha256']:
        raise ValueError('External evidence hash differs')
    bundle = joblib.load(io.BytesIO(raw))
    if bundle.get('model_family') != 'biochemical_v4' or bundle.get('clinicalValidated') is not False or bundle.get('features') != FEATURES:
        raise ValueError('Invalid biochemical model scope/schema')
    if bundle['dictionary_file_sha256'] != sha(ROOT/'data/feature_dictionary.json'):
        raise ValueError('Feature dictionary differs')
    for endpoint in ENDPOINTS:
        for route in ['extended','cbc']:
            head = bundle['heads'].get((endpoint,route))
            threshold = bundle['thresholds'].get((endpoint,route))
            if not isinstance(head,BiochemicalClassifier) or head.endpoint != endpoint or head.route != route:
                raise ValueError('Invalid biochemical head')
            if not isinstance(threshold,(int,float)) or not np.isfinite(threshold) or not 0 <= threshold <= np.nextafter(1.,np.inf):
                raise ValueError('Invalid research threshold')
    bundle['validation'] = json.loads(gates_path.read_text())
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,default=DEFAULT_BUNDLE)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--pregnancy',choices=['unknown','not_pregnant','pregnant'],default='unknown')
    args = parser.parse_args()
    result = predict_biochemical(load_candidate(args.bundle),json.loads(args.input.read_text()),args.pregnancy)
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))


if __name__ == '__main__':
    main()
