import {config} from './config.js';
import {escapeHTML as e} from './export.js';

export function createAuthClient(settings=config,request=globalThis.fetch){
  async function call(path,body){
    const response=await request(settings.baseUrl+'/api/auth/'+path,{method:body===undefined?'GET':'POST',credentials:'include',headers:body===undefined?undefined:{'Content-Type':'application/json','X-Hema-Client':'1'},body:body===undefined?undefined:JSON.stringify(body)});
    let result;try{result=await response.json();}catch{throw new Error('Не удалось связаться с сервисом входа. Попробуйте ещё раз.');}
    if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Сервис входа временно недоступен. Попробуйте ещё раз.');
    if(typeof result.authenticated!=='boolean'||(result.authenticated&&(!result.user||typeof result.user.id!=='string'||typeof result.user.username!=='string'||typeof result.user.displayName!=='string'||!Number.isFinite(result.user.expiresAt))))throw new Error('Сервис вернул некорректный ответ входа.');
    return result;
  }
  return {session:()=>call('session'),login:(username,password)=>call('login',{username,password}),logout:()=>call('logout',{})};
}

export function installDoctorAccess({doctorPage=false,onReady=()=>{}}={}){
  const auth=createAuthClient();
  const main=document.querySelector('main');
  if(doctorPage){main.hidden=true;main.inert=true;}
  document.body.insertAdjacentHTML('beforeend',`<dialog id="doctor-login-dialog" class="auth-dialog" aria-labelledby="doctor-login-title" aria-describedby="doctor-login-description"><button type="button" class="close auth-close" aria-label="Закрыть окно входа">×</button><span class="eyebrow">ДОСТУП ДЛЯ СПЕЦИАЛИСТОВ</span><h2 id="doctor-login-title">Вход для врача</h2><p id="doctor-login-description">Войдите с логином и паролем, которые выдала ваша организация. Доступ предназначен для специалистов, работающих с лабораторными анализами.</p><form id="doctor-login-form"><label class="field" for="doctor-login">Логин<input id="doctor-login" name="username" autocomplete="username" autofocus autocapitalize="none" spellcheck="false" maxlength="64" required></label><div class="field"><label for="doctor-password">Пароль</label><div class="auth-password"><input id="doctor-password" name="password" type="password" autocomplete="current-password" maxlength="128" required><button type="button" id="auth-show-password" aria-pressed="false">Показать</button></div></div><p id="auth-error" class="error" role="alert" hidden></p><button type="submit" class="primary" id="auth-submit">Войти <span>→</span></button></form><div class="auth-help"><strong>Нет доступа или забыли пароль?</strong><p>Обратитесь к администратору своей организации. Самостоятельная регистрация и восстановление пароля на сайте не предусмотрены.</p><p>Сеанс действует до 8 часов. На общем компьютере завершите работу кнопкой «Выйти». Пациентский режим доступен без входа.</p></div><a class="text-button" href="./patient.html">Продолжить как пациент ↗</a></dialog>`);
  const dialog=document.getElementById('doctor-login-dialog');
  const form=document.getElementById('doctor-login-form');
  const error=document.getElementById('auth-error');
  const loginButton=document.getElementById('header-login');
  let pending=false,initialized=false,user=null,timer;
  function show(message=''){
    document.getElementById('doctor-password').value='';error.textContent=message;error.hidden=!message;
    if(!dialog.open)dialog.showModal();
  }
  function ready(profile){
    if(doctorPage&&user&&user.id!==profile.id){location.reload();return;}
    user=profile;
    if(!doctorPage){location.assign('./doctor.html');return;}
    if(loginButton)loginButton.hidden=true;
    dialog.close();main.hidden=false;main.inert=false;
    if(!initialized){initialized=true;onReady();}
    let identity=document.getElementById('doctor-identity');
    if(!identity){identity=document.createElement('div');identity.id='doctor-identity';identity.className='doctor-identity';document.querySelector('.header nav').append(identity);}
    identity.innerHTML=`<span>${e(user.displayName)}</span><button type="button" class="text-button" id="doctor-logout">Выйти</button>`;
    document.getElementById('doctor-logout').onclick=async()=>{
      const button=document.getElementById('doctor-logout');button.disabled=true;
      try{await auth.logout();location.replace('./index.html');}catch(err){button.disabled=false;show(err.message);}
    };
    clearTimeout(timer);timer=setTimeout(()=>location.reload(),Math.max(0,profile.expiresAt*1000-Date.now()));
  }
  async function enter(){
    if(pending)return;pending=true;
    try{
      if(config.mode!=='api')throw new Error('Для входа откройте Hema через локальный сервер.');
      const result=await auth.session();if(result.authenticated)ready(result.user);else show();
    }catch(err){show(err.message);}finally{pending=false;}
  }
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(pending)return;pending=true;const button=document.getElementById('auth-submit');button.disabled=true;error.hidden=true;
    try{if(config.mode!=='api')throw new Error('Для входа откройте Hema через локальный сервер.');const result=await auth.login(form.username.value,form.password.value);form.password.value='';ready(result.user);}
    catch(err){form.password.value='';error.textContent=err.message;error.hidden=false;form.password.focus();}
    finally{pending=false;button.disabled=false;}
  });
  document.getElementById('auth-show-password').onclick=event=>{
    const input=document.getElementById('doctor-password');const shown=input.type==='password';input.type=shown?'text':'password';event.currentTarget.textContent=shown?'Скрыть':'Показать';event.currentTarget.setAttribute('aria-pressed',String(shown));
  };
  function cancel(){if(pending)return;if(doctorPage)location.replace('./index.html');else dialog.close();}
  dialog.querySelector('.auth-close').onclick=cancel;
  dialog.addEventListener('cancel',event=>{event.preventDefault();cancel();});
  dialog.addEventListener('close',()=>{form.password.value='';document.getElementById('doctor-password').type='password';document.getElementById('auth-show-password').textContent='Показать';document.getElementById('auth-show-password').setAttribute('aria-pressed','false');});
  document.addEventListener('hema:authentication-required',()=>{if(doctorPage)location.reload();});
  window.addEventListener('pagehide',()=>{if(doctorPage){main.hidden=true;main.inert=true;}});
  window.addEventListener('pageshow',event=>{if(doctorPage&&event.persisted)location.reload();});
  window.addEventListener('focus',async()=>{if(!doctorPage||!user||pending)return;try{const session=await auth.session();if(!session.authenticated||session.user.id!==user.id)location.reload();}catch{/* Keep the view; API requests still enforce authentication. */}});
  if(doctorPage)enter();
  else document.querySelector('.doctor-card')?.addEventListener('click',event=>{if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault();enter();});
  loginButton?.addEventListener('click',enter);
}
