"""Audited cohort preparation for v4. This module does not fit models."""
from copy import deepcopy
import json

import numpy as np
import pandas as pd

from .core import ROOT, FEATURES, CBC, encode
from .external_validate import sha, write_json, fingerprints
from scripts.prepare_nhanes import convert


def new_mapping(cycle):
    year, suffix = {'nhanes_2007_2008':(2007,'E'),'nhanes_2013_2014':(2013,'H')}[cycle]
    mapping = json.loads((ROOT/'data/external/unit_mapping.json').read_text())['sources']['nhanes_2003_2004']['entries']
    keep = CBC + ['creatinine','albumin','LDH'] + (['CRP','ferritin','vitamin_B6'] if suffix == 'E' else ['vitamin_B12'])
    result = []
    for original in mapping:
        if original['target'] not in keep:
            continue
        rule = deepcopy(original)
        target = rule['target']
        table = ('DEMO' if target in {'age_years','sex'} else 'CBC' if target in CBC else
                 'BIOPRO' if target in {'creatinine','albumin','LDH'} else
                 {'CRP':'CRP','ferritin':'FERTIN','vitamin_B6':'VIT_B6','vitamin_B12':'VITB12'}[target])
        rule['table'] = f'{table}_{suffix}'
        if target == 'vitamin_B6':
            rule.update(column='LBXPLP', notes='Published serum HPLC PLP; early enzymatic PLP excluded.')
        elif target == 'vitamin_B12':
            rule.update(column='LBDB12', notes='Use published reagent-lot-corrected result, without a second correction.')
        elif target == 'age_years':
            rule['notes'] = 'Public-use age top-coded at 80; predictor coarsening is fixed separately.'
        elif target == 'ferritin':
            rule['notes'] = 'Roche/Hitachi; adult reference only in women 18–49 after eligibility filter.'
        rule['source_url'] = f'https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/{year}/DataFiles/{rule["table"]}.htm'
        result.append(rule)
    return result


def pregnancy_exclusion(frame, metadata):
    code = metadata.pregnancy_code
    unknown = frame.sex.eq('F') & frame.age_years.between(18,44) & ~code.isin([1,2])
    return code.eq(1) | unknown


def existing_cohort(cycle):
    if cycle == 'nhanes_2005_2006':
        folder = ROOT/'experiments/external_validation_v1'
        f = pd.read_csv(folder/'features.csv')
        audit = pd.read_csv(folder/'cohort_audit.csv').set_index('patient_id',verify_integrity=True)
        meta = audit.loc[f.patient_id].reset_index()
    else:
        folder = ROOT/'data/external/processed/nhanes_case_units_v1'
        f = pd.read_csv(folder/f'{cycle}_features.csv')
        original = pd.read_csv(folder/f'{cycle}_metadata.csv').set_index('patient_id',verify_integrity=True).loc[f.patient_id].reset_index()
        suffix = 'C' if cycle == 'nhanes_2003_2004' else 'G'
        meta = original[['patient_id','pregnancy_code']].copy()
        for dest, column in [('stratum','SDMVSTRA'),('psu','SDMVPSU'),('WTMEC2YR','WTMEC2YR')]:
            meta[dest] = original[f'DEMO_{suffix}__{column}']
    keep = ~pregnancy_exclusion(f,meta)
    f, meta = f.loc[keep].reset_index(drop=True), meta.loc[keep].reset_index(drop=True)
    f['cycle'] = cycle
    meta['cycle'] = cycle
    meta['group'] = cycle + '_stratum_' + meta.stratum.astype(str) + '_PSU_' + meta.psu.astype(str)
    return f,meta


def test_cohort(cycle, mapping):
    folder = ROOT/'data/external/raw'/cycle
    manifest = json.loads((folder/'download_manifest.json').read_text())
    for item in manifest:
        if sha(ROOT/item['path']) != item['sha256']:
            raise ValueError('Test source hash differs')
    tables = {item['path'].split('/')[-1].removesuffix('.xpt'):
              pd.read_sas(ROOT/item['path'],format='xport').set_index('SEQN',verify_integrity=True) for item in manifest}
    suffix = 'E' if cycle == 'nhanes_2007_2008' else 'H'
    demo = tables[f'DEMO_{suffix}']
    result = pd.DataFrame(np.nan,index=demo.index,columns=FEATURES)
    result['sex'] = pd.Series(index=demo.index,dtype=object)
    for rule in mapping:
        result[rule['target']] = convert(tables[rule['table']][rule['column']].reindex(demo.index),rule)
    ids = [f'{cycle}_SEQN_{int(i)}' for i in demo.index]
    result.insert(0,'patient_id',ids)
    result['cycle'] = cycle
    meta = pd.DataFrame({'patient_id':ids,'cycle':cycle,'pregnancy_code':demo.RIDEXPRG,
                         'stratum':demo.SDMVSTRA,'psu':demo.SDMVPSU,'WTMEC2YR':demo.WTMEC2YR},index=demo.index)
    keep = result.age_years.ge(18) & ~pregnancy_exclusion(result,meta)
    f,meta = result.loc[keep].reset_index(drop=True),meta.loc[keep].reset_index(drop=True)
    meta['group'] = cycle + '_stratum_' + meta.stratum.astype(str) + '_PSU_' + meta.psu.astype(str)
    return f,meta,{'source_rows':len(demo),'eligible_adults':len(f),'source_sha256':{r['path']:r['sha256'] for r in manifest}}


def audit_test(f,meta,development,case,output,cycle):
    x = encode(f)
    known = x[CBC].notna().all(axis=1)
    reference = pd.concat([encode(development),encode(case)],ignore_index=True)
    reference = reference.loc[reference[CBC].notna().all(axis=1)]
    overlap = known & fingerprints(x).isin(fingerprints(reference))
    duplicate = f.duplicated(FEATURES) & f[CBC[2:]].notna().any(axis=1)
    keep = ~overlap & ~duplicate
    audit = meta.copy()
    audit['exact_complete_CBC_match'] = overlap
    audit['duplicate'] = duplicate
    audit.to_csv(output/f'{cycle}_test_audit.csv',index=False)
    return f.loc[keep].reset_index(drop=True),meta.loc[keep].reset_index(drop=True),{'exact_CBC_matches_excluded':int(overlap.sum()),'duplicates_excluded':int(duplicate.sum())}
