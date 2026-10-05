import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createClient} from '../src/api.js';
import {config} from '../src/config.js';
import {recommendationDetails} from '../src/recommendation-details.js';
const inputs={sex:'F',age_years:42,hemoglobin:108};
test('recommendation response keeps separate conclusions and insufficient-data output',async()=>{
  const response={items:[{id:'p',text:'Следующий шаг'}],status:'draft',clinicalReviewStatus:'draft',phraseFileVersion:'draft-1',
    conclusions:[{id:'c',text:'Вывод по Hb'}],dataSufficiency:{level:'insufficient',summary:'Мало данных: 1 из 35'},
    insufficientData:{active:true,items:[{id:'d',text:'Добавьте доступные показатели'}]},warnings:['Черновик']};
  const client=createClient({...config,mode:'api'},async url=>({ok:true,json:async()=>url===config.paths.recommendations?response:{reportId:'r',audience:'patient',anemia:true,modelConnected:false}}));
  const result=await client.predict('patient',inputs);
  assert.equal(result.recommendationStatus,'draft');assert.equal(result.clinicalReviewStatus,'draft');
  assert.equal(result.recommendations.length,1);assert.equal(result.recommendationConclusions[0].text,'Вывод по Hb');
  assert.equal(result.insufficientData.active,true);assert.equal(result.dataSufficiency.level,'insufficient');
  const html=recommendationDetails(result);
  assert.match(html,/Недостаточно данных/);assert.match(html,/1 из 35/);assert.match(html,/Вывод по Hb/);
});
test('data-gap and conclusion text is escaped in real and demonstration reports',()=>{
  const report={source:'api-rule',recommendationConclusions:[{text:'<script>alert(1)</script>'}],dataSufficiency:{level:'limited',summary:'<img src=x>'},insufficientData:{items:[{text:'<b>gap</b>'}]}};
  const html=recommendationDetails(report);assert.doesNotMatch(html,/<script>|<img|<b>/);assert.match(html,/&lt;script&gt;/);
  assert.equal(recommendationDetails({...report,source:'demo'}),html);assert.equal(recommendationDetails({source:'local-rule'}),'');
});
test('invalid recommendation details cause an error instead of displaying unsupported output',async()=>{
  for(const bad of [{conclusions:'text'}, {insufficientData:{items:[{text:2}]}}, {dataSufficiency:{summary:'text',level:'certain'}}]){
    const client=createClient({...config,mode:'api'},async url=>({ok:true,json:async()=>url===config.paths.recommendations?{items:[],...bad}:{reportId:'r',audience:'patient',anemia:true,modelConnected:false}}));
    await assert.rejects(client.predict('patient',inputs),/формат|полноты/);
  }
});
