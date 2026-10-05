import {test} from 'node:test';
import assert from 'node:assert/strict';
import {screen,parseCSV,numeric} from '../src/screening.js';
const base={age_years:42,sex:'F',hemoglobin:120};
test('sex-specific strict Hb boundaries',()=>{assert.equal(screen(base).anemia,false);assert.equal(screen({...base,hemoglobin:119.9}).anemia,true);assert.equal(screen({...base,sex:'M',hemoglobin:129.9}).anemia,true);assert.equal(screen({...base,sex:'M',hemoglobin:130}).anemia,false);});
test('missing values stay null and normal Hb does not rule out deficiency',()=>{const result=screen(base);assert.equal(result.inputs.ferritin,null);assert.equal(result.deficiencies,null);assert.equal(result.modelConnected,false);assert.equal(numeric('NaN'),null);assert.equal(numeric('0'),0);assert.equal(numeric('12,3'),12.3);});
test('invalid values and required fields are rejected',()=>{for(const values of [{...base,age_years:17},{...base,sex:''},{...base,hemoglobin:''},{...base,ferritin:'bad'},{...base,hemoglobin:-1}])assert.throws(()=>screen(values));});
test('CSV supports BOM, semicolon, decimal comma, CRLF and ignores target labels',()=>{const [record]=parseCSV('\uFEFFage_years;sex;hemoglobin;ferritin;anemia;patient_id\r\n42;F;"119,5";;0;private-id\r\n');assert.equal(screen(record).anemia,true);assert.equal(screen(record).inputs.ferritin,null);assert.equal('anemia' in record,false);assert.equal('patient_id' in record,false);});
test('CSV supports quotes and rejects corrupt structures',()=>{const rows=parseCSV('age_years,sex,hemoglobin,ignored\n42,F,120,"hello, ""world"""');assert.equal(rows.length,1);for(const csv of ['age_years,sex\n42,F','age_years,sex,hemoglobin\n42,F','age_years,sex,hemoglobin\n42,F,"120','age_years,sex,sex,hemoglobin\n42,F,F,120'])assert.throws(()=>parseCSV(csv));});
