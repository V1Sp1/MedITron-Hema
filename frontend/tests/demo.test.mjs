import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {demoReport,demoScenarios} from '../src/demo.js';
import {recommendationDetails} from '../src/recommendation-details.js';
import {demoNotice,inputDetailsHTML,reportInputRows,scoreAvailabilityMessage} from '../src/report-details.js';
import {escapeHTML} from '../src/export.js';
import {screen,parseCSV} from '../src/screening.js';

const specific=new Set(['iron','B12','folate','B6','copper','mixed','inflammation','other']);

test('15 actual model snapshots preserve role-specific current phrases and canonical inputs',()=>{
  assert.equal(demoScenarios.length,15);
  assert.equal(new Set(demoScenarios.map(item=>item.id)).size,15);
  for(const role of ['patient','doctor']){
    const catalog=JSON.parse(readFileSync(new URL(`../../backend/data/recommendations_${role}.json`,import.meta.url),'utf8'));
    const byId=new Map(catalog.items.map(item=>[item.id,item]));
    for(const {id} of demoScenarios){
      const report=demoReport(role,id);
      assert.equal(report.source,'demo');assert.equal(report.modelConnected,true);
      assert.equal(report.simulation,false);assert.equal(report.demoModelComputed,true);
      assert.match(report.modelVersion,/^hema-baseline-v1-/);
      assert.equal(report.demoProvenance.modelVersion,report.modelVersion);
      assert.equal(report.demoProvenance.inputSource,'authored_synthetic_panel');
      assert.equal(report.recommendationVersion,catalog.version);
      assert.deepEqual(report.inputs,screen(report.inputs).inputs);
      for(const item of [...report.recommendations,...report.recommendationConclusions,...report.insufficientData.items]){
        const current=byId.get(item.id);assert.ok(current);
        assert.ok(item.id.startsWith(role+'.'));
        if(current.topic==='mixed')assert.equal(item.text,current.text.replace('{deficits}','витамин B12, железо'));
        else assert.equal(item.text,current.text);
        for(const key of item.suggestedFeatures)assert.equal(report.inputs[key],null);
      }
      if(specific.has(id)){
        assert.equal(report.modelDecisionState,'evaluated');
        assert.ok(report.recommendations.some(item=>item.topic===id));
      }
      assert.match(recommendationDetails(report),/Что удалось оценить/);
      assert.match(demoNotice(report),/сохранённый расчёт локальной модели/);
    }
  }
});

test('numeric scores and model decisions are identical across roles and retained under uncertainty',()=>{
  for(const {id} of demoScenarios){
    const patient=demoReport('patient',id),doctor=demoReport('doctor',id);
    for(const key of ['inputs','prediction','modelVerdict','modelDecisionState','deficiencyScores','anemiaScores'])
      assert.deepEqual(patient[key],doctor[key]);
    if(id==='sparse')continue;
    assert.equal(doctor.deficiencyScores.length,5);
    assert.equal(doctor.anemiaScores.length,doctor.anemia?12:0);
    for(const item of [...doctor.deficiencyScores,...doctor.anemiaScores])assert.ok(Number.isFinite(item.score)&&item.score>=0&&item.score<=1);
    if(doctor.anemia)assert.ok(Math.abs(doctor.anemiaScores.reduce((sum,item)=>sum+item.score,0)-1)<1e-6);
    if(doctor.modelDecisionState!=='evaluated'){
      assert.equal(doctor.prediction.code,null);
      assert.ok(doctor.decisionReasonCodes.length);
      assert.ok(!doctor.recommendations.some(item=>specific.has(item.topic)));
    }
  }
  assert.equal(demoReport('doctor','conflict').modelDecisionState,'inconsistent');
  assert.equal(demoReport('doctor','cbc').modelPanel,'cbc');
});

test('sparse panel states the reason for absent scores without claiming disconnected model',()=>{
  for(const role of ['patient','doctor']){
    const report=demoReport(role,'sparse');
    assert.equal(report.dataSufficiency.laboratoryCount,1);
    assert.equal(report.insufficientData.active,true);
    assert.equal(report.modelDecisionState,'suppressed_sparse');
    assert.deepEqual(report.recommendations.map(item=>item.topic),['follow_up']);
    assert.ok(report.insufficientData.items.some(item=>item.topic==='sparse'));
    assert.deepEqual(report.deficiencyScores,[]);assert.deepEqual(report.anemiaScores,[]);
    assert.match(scoreAvailabilityMessage(report),/слишком мало анализов/);
    assert.doesNotMatch(scoreAvailabilityMessage(report),/не подключена/);
  }
});

test('both exact Hb boundaries exclude anemia; independent copies protect scenario data',()=>{
  for(const id of ['B12_boundary','male_boundary']){
    const report=demoReport('patient',id);
    assert.equal(report.hemoglobin,report.threshold);assert.equal(report.anemia,false);
    report.recommendations[0].text='mutated';report.inputs.hemoglobin=0;
    assert.notEqual(demoReport('patient',id).inputs.hemoglobin,0);
    assert.notEqual(demoReport('patient',id).recommendations[0].text,'mutated');
  }
  assert.throws(()=>demoReport('admin'),/роль/);
  assert.throws(()=>demoReport('patient','unknown'),/пример/);
});

test('full example table shows all 35 units, escapes metadata and distinguishes null from zero',()=>{
  const report=demoReport('doctor','sparse');
  report.inputs.CRP=0;report.modelVersion='<script>unsafe</script>';
  const html=inputDetailsHTML(report,escapeHTML);
  assert.equal(reportInputRows(report).length,35);
  assert.match(html,/2\/35/);assert.match(html,/не указан/);assert.match(html,/<td>0<\/td>/);
  assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
});

test('downloadable CSV imports exactly the same 15 panels as the example selector',()=>{
  const rows=parseCSV(readFileSync(new URL('../../examples/full-demo/input_panels.csv',import.meta.url),'utf8'));
  assert.equal(rows.length,demoScenarios.length);
  rows.forEach((row,index)=>assert.deepEqual(screen(row).inputs,demoReport('doctor',demoScenarios[index].id).inputs));
});
