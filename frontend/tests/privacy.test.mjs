import test from 'node:test';
import assert from 'node:assert/strict';
import {createPrivacyClient} from '../src/privacy.js';
import {createClient} from '../src/api.js';

test('research acknowledgement sends explicit version, kind and cookies without a legal-consent claim',async()=>{
  const calls=[];const fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({acknowledged:true})};};
  const api=createPrivacyClient({baseUrl:'http://localhost:8000'},fetch);
  await api.status();await api.acknowledge('version','synthetic');
  assert.equal(calls[0].options.method,'GET');
  assert.deepEqual(JSON.parse(calls[1].options.body),{policyVersion:'version',dataKind:'synthetic',researchOnly:true});
  assert.equal(calls[1].options.credentials,'include');assert.equal(calls[1].options.headers['X-Hema-Client'],'1');
});

test('server rejection stays an error and session clear uses authenticated DELETE',async()=>{
  const failed=createPrivacyClient({baseUrl:''},async()=>({ok:false,json:async()=>({detail:'research only'})}));
  await assert.rejects(()=>failed.acknowledge('v','anonymized'),/research only/);
  let call;const client=createClient({mode:'api',baseUrl:''},async(url,options)=>{call={url,options};return {ok:true,json:async()=>({deleted:true})};});
  assert.deepEqual(await client.clearSession(),{deleted:true});
  assert.equal(call.url,'/api/privacy/session');assert.equal(call.options.method,'DELETE');
  assert.equal(call.options.credentials,'include');assert.equal(call.options.headers['X-Hema-Client'],'1');
});

test('failed server deletion is visible and never presented as a successful clear',async()=>{
  const client=createClient({mode:'api',baseUrl:''},async()=>({ok:false,status:503}));
  await assert.rejects(()=>client.clearSession(),/Не удалось удалить/);
});
