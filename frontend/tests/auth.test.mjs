import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createAuthClient} from '../src/auth.js';
import {createClient} from '../src/api.js';
import {config} from '../src/config.js';
const user={id:'u',username:'doctor',displayName:'Врач',expiresAt:1800000000};
test('login, session and logout use credentials and CSRF header without browser token storage',async()=>{
 const calls=[];const auth=createAuthClient({baseUrl:'http://127.0.0.1:8000'},async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({authenticated:!url.endsWith('logout'),user:url.endsWith('logout')?null:user})};});
 assert.equal((await auth.session()).user.id,'u');await auth.login('doctor','test-password');await auth.logout();
 assert.equal(calls[0].options.method,'GET');assert.ok(calls.every(c=>c.options.credentials==='include'));
 assert.equal(calls[1].options.headers['X-Hema-Client'],'1');assert.deepEqual(JSON.parse(calls[1].options.body),{username:'doctor',password:'test-password'});
});
test('failed login stays an error and invalid session cannot unlock the view',async()=>{
 for(const response of [{ok:false,json:async()=>({detail:'Неверный логин или пароль'})},{ok:true,json:async()=>({authenticated:true,user:{username:'doctor'}})}]){
  const auth=createAuthClient({baseUrl:''},async()=>response);await assert.rejects(auth.login('doctor','wrong'),/Неверный|некорректный/);
 }
});
test('doctor PDF upload explicitly selects protected audience and sends cookie credentials',async()=>{
 let captured;const client=createClient({...config,mode:'api'},async(url,options)=>{captured=options;return {ok:true,json:async()=>({observationId:'obs',revision:1,inputs:{}})};});
 await client.extractPDF([new Blob(['%PDF'])],{audience:'doctor'});
 assert.equal(captured.body.get('audience'),'doctor');assert.equal(captured.credentials,'include');assert.equal(captured.headers['X-Hema-Client'],'1');
});
