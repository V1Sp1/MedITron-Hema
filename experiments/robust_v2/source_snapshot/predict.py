"""Research inference for trusted local bundles, also used by the backend adapter."""

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from .core import CBC, DICTIONARY, FEATURES, LABS, TARGETS, anemia_status, encode, target_probabilities


def predict(bundle, inputs, panel='auto'):
    if panel not in {'auto','primary','cbc'}:
        raise ValueError('panel must be auto, primary or cbc')
    if set(inputs)-set(FEATURES):
        raise ValueError('Unexpected fields, identifiers or targets in model input')
    if bundle['features']!=FEATURES or bundle['dictionary_sha256']!=DICTIONARY['source']['sha256']:
        raise ValueError('Model schema or unit dictionary differs')
    for key,value in inputs.items():
        if key=='sex':
            if value not in {'F','M',None}: raise ValueError('sex must be F/M/null')
        elif value is not None and (type(value) not in {int,float} or not np.isfinite(value) or value<0):
            raise ValueError(f'{key}: expected nonnegative finite numeric value or null')
    frame=encode(pd.DataFrame([inputs]))
    available=[k for k in LABS if frame[k].notna().iloc[0]]
    anemia=anemia_status(frame).iloc[0]
    result={'research_only':True,'calibrated':False,'anemia':None if pd.isna(anemia) else bool(anemia),
            'available_lab_count':len(available),'missing':[k for k in FEATURES if pd.isna(frame[k].iloc[0])],
            'scores':{},'warnings':['Модели исследовательские, scores не откалиброваны. Отсутствие анализа не считается нормой.']}
    if not available:
        result.update(status='insufficient_data',panel=None)
        return result
    choice='cbc' if panel=='auto' and all(k in CBC for k in available) else 'primary' if panel=='auto' else panel
    result.update(status='research_prediction',panel=choice)
    for target in TARGETS:
        name=bundle['selected'][target][choice]
        model=bundle['models'][(name,target)]
        labels=bundle['labels'][target]
        p=target_probabilities(model,frame,bundle['configs'][name],labels,target)[0]
        result['scores'][target]={'model':name,'values':{str(label):float(value) for label,value in zip(labels,p)}}
    if len(available)<=1:
        result['warnings'].append('Введён один лабораторный показатель; причину и дефициты нельзя считать установленными.')
    if result['anemia'] is None:
        result['warnings'].append('Нет Hb или пола; статус анемии по правилу кейса неизвестен.')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--panel',choices=['auto','primary','cbc'],default='auto')
    args=parser.parse_args()
    print(json.dumps(predict(joblib.load(args.bundle),json.loads(args.input.read_text()),args.panel),ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__':
    main()
