import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createClient} from '../src/api.js';
import {config} from '../src/config.js';
const inputs={sex:'F',age_years:42,hemoglobin:108};
const settings={...config,mode:'api'};
test('API rule remains clearly distinct from a connected model',async()=>{
  const client=createClient(settings,async url=>({ok:true,json:async()=>url===config.paths.recommendations
    ?{items:[],phraseFileVersion:null,warnings:['Файл фраз не подключён']}
    :{reportId:'r',audience:'doctor',anemia:true,modelConnected:false,prediction:null,warnings:['Нет модели'],deficiencyProbabilities:[]}}));
  const report=await client.predict('doctor',inputs);
  assert.equal(report.source,'api-rule');assert.equal(report.modelConnected,false);
  assert.deepEqual(report.recommendations,[]);assert.deepEqual(report.warnings,['Нет модели','Файл фраз не подключён']);
});
test('PDF form confirmation sends current revision and advances it for another report',async()=>{
  let revision=1;
  const client=createClient(settings,async(url,options)=>{
    if(url===config.paths.pdf)return {ok:true,json:async()=>({observationId:'obs',revision:1,inputs})};
    if(url===config.paths.recommendations)return {ok:true,json:async()=>({items:[],warnings:[]})};
    const body=JSON.parse(options.body);
    assert.equal(body.observationRevision,revision);assert.equal(body.reviewedObservation,true);
    revision++;
    return {ok:true,json:async()=>({reportId:'r',audience:'patient',anemia:true,modelConnected:false,observationRevision:revision})};
  });
  await client.extractPDF([new File(['%PDF'],'lab.pdf')]);
  await client.predict('patient',inputs,{observationId:'obs'});
  await client.predict('patient',inputs,{observationId:'obs'});
  assert.equal(revision,3);
  await assert.rejects(client.predict('patient',inputs,{observationId:'unknown'}),/Загрузите PDF/);
});
test('backend validation details reach the form; failed upload is not accepted',async()=>{
  await assert.rejects(createClient(settings,async()=>({ok:false,status:422,json:async()=>({detail:'Укажите гемоглобин в г/л.'})})).predict('doctor',inputs),/Укажите гемоглобин/);
  await assert.rejects(createClient(settings,async()=>({ok:true,json:async()=>({observationId:'obs',inputs})})).extractPDF([]),/Неверный формат/);
});
