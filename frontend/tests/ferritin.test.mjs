import test from 'node:test';
import assert from 'node:assert/strict';
import {createClient} from '../src/api.js';
import {config} from '../src/config.js';
import {validateFerritin,ferritinDetails,ferritinDetailsHTML} from '../src/ferritin.js';
import {escapeHTML,reportHTML} from '../src/export.js';

const prediction={endpoint:'low_ferritin',status:'research_prediction',score:.6,decisionThreshold:.259,
  screenPositive:true,route:'extended',markerUsedAsPredictor:false,externalGatePassed:true,
  scoreMeaning:'research_score_for_low_biochemical_marker',clinicalValidated:false,
  message:'Оценка выше исследовательского порога.',limitation:'Низкая оценка не исключает дефицит.',unit:'µg/L'};

test('pregnancy context is sent separately and never becomes a laboratory predictor',async()=>{
  const calls=[];
  const client=createClient({...config,mode:'api'},async(url,options)=>{
    calls.push(JSON.parse(options.body));
    return {ok:true,json:async()=>url===config.paths.recommendations?{items:[]}:
      {audience:'patient',reportId:'r',anemia:true,ferritinScreening:prediction}};
  });
  const inputs={age_years:40,sex:'F',hemoglobin:110,pregnancyStatus:'not_pregnant'};
  const result=await client.predict('patient',inputs);
  assert.equal(calls[0].pregnancyStatus,'not_pregnant');
  assert.equal('pregnancyStatus' in calls[0].inputs,false);
  assert.equal(result.ferritinScreening.status,'research_prediction');
  await client.predict('patient',{age_years:40,sex:'F',hemoglobin:110});
  assert.equal(calls[2].pregnancyStatus,'unknown');
  await assert.rejects(client.predict('patient',{...inputs,pregnancyStatus:'no'}),/беременности/);
});

test('incompatible marker predictions are rejected instead of appearing normal',()=>{
  validateFerritin(prediction);
  for(const patch of [{score:NaN},{score:1.1},{screenPositive:false},{route:'primary'},
    {clinicalValidated:true},{markerUsedAsPredictor:true},{externalGatePassed:false}])
    assert.throws(()=>validateFerritin({...prediction,...patch}));
  assert.throws(()=>validateFerritin({...prediction,status:'outside_scope'}));
  assert.throws(()=>validateFerritin({endpoint:'low_ferritin',status:'observed',observedValue:9,
    score:.7,clinicalValidated:false,message:'Измерен'}));
  validateFerritin({endpoint:'low_ferritin',status:'unknown_pregnancy',clinicalValidated:false,message:'Укажите статус'});
  validateFerritin(undefined);
});

test('marker is a separate section, doctor sees scores and patient sees explanation',()=>{
  const patient={audience:'patient',ferritinScreening:prediction};
  assert.doesNotMatch(ferritinDetails(patient).paragraphs.join(' '),/Оценка модели:|60%/);
  const doctor={...patient,audience:'doctor'};
  assert.match(ferritinDetails(doctor).paragraphs.join(' '),/0\.600.*0\.259/);
  assert.match(ferritinDetailsHTML(doctor,escapeHTML),/Прогноз низкого ферритина/);
  assert.match(reportHTML(doctor),/Прогноз низкого ферритина/);
  const measured={audience:'patient',ferritinScreening:{endpoint:'low_ferritin',status:'observed',
    observedValue:9,unit:'µg/L',clinicalValidated:false,message:'Показан результат анализа'}};
  assert.match(ferritinDetailsHTML(measured,escapeHTML),/Ферритин: результат анализа.*9 µg\/L/);
  assert.doesNotMatch(ferritinDetailsHTML(measured,escapeHTML),/Прогноз низкого ферритина|Оценка модели:/);
  assert.equal(ferritinDetails({}),null);
});

test('unavailable and below-threshold outputs stay explicit; marker text is escaped',()=>{
  const missing={audience:'doctor',ferritinScreening:{status:'unknown_pregnancy',message:'Укажите статус беременности'}};
  assert.match(ferritinDetailsHTML(missing,escapeHTML),/Укажите статус беременности/);
  const negative={audience:'patient',ferritinScreening:{...prediction,score:.1,screenPositive:false,
    message:'Это не исключает низкий ферритин или дефицит железа. <script>x</script>'}};
  const html=ferritinDetailsHTML(negative,escapeHTML);
  assert.match(html,/не исключает/);
  assert.match(html,/&lt;script&gt;/);
  assert.doesNotMatch(html,/<script>/);
});
