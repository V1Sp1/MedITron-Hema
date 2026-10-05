import {config} from './config.js';
import {screen} from './screening.js';
import {validateFerritin} from './ferritin.js';
const roles=['patient','doctor'];
export function createClient(settings=config,request=globalThis.fetch) {
  const revisions=new Map();
  async function post(path,body,signal) {
    const multipart=typeof FormData!=='undefined'&&body instanceof FormData;
    const response=await request(settings.baseUrl+path,{method:'POST',credentials:'include',body:multipart?body:JSON.stringify(body),headers:multipart?{'X-Hema-Client':'1'}:{'Content-Type':'application/json','X-Hema-Client':'1'},signal});
    if(!response.ok){
      if(response.status===401&&typeof document!=='undefined')document.dispatchEvent(new Event('hema:authentication-required'));
      if(response.status===428&&typeof document!=='undefined')document.dispatchEvent(new Event('hema:research-required'));
      let detail;try{detail=(await response.json()).detail;}catch{}
      throw new Error(typeof detail==='string'?detail:`Сервис временно недоступен (${response.status}). Попробуйте повторить запрос.`);
    }
    return response.json();
  }
  return {
    async predict(role,values,{signal,observationId=null,pregnancyStatus=values.pregnancyStatus??'unknown'}={}) {
      if(!roles.includes(role))throw new Error('Неизвестная роль.');
      if(!['unknown','not_pregnant','pregnant'].includes(pregnancyStatus))throw new Error('Неизвестный статус беременности.');
      const rule=screen(values); // normalized whitelist, preserves missingness and removes target labels
      if(settings.mode==='local')return {...rule,audience:role,source:'local-rule',prediction:null,deficiencyProbabilities:[],anemiaProbabilities:[],recommendations:[],warnings:[]};
      const review=observationId?{observationRevision:revisions.get(observationId),reviewedObservation:true}:{};
      if(observationId&&!Number.isInteger(review.observationRevision))throw new Error('Загрузите PDF заново и проверьте распознанные значения.');
      const result=await post(settings.paths[role],{inputs:rule.inputs,observationId,pregnancyStatus,...review},signal);
      if(result.audience!==role||typeof result.reportId!=='string'||!result.reportId||typeof result.anemia!=='boolean')throw new Error('Сервис вернул несовместимый формат отчёта.');
      if(result.anemia!==rule.anemia)throw new Error('Статус анемии от сервиса не согласуется с порогом Hb из кейса.');
      if(observationId&&Number.isInteger(result.observationRevision))revisions.set(observationId,result.observationRevision);
      if(result.warnings!=null&&(!Array.isArray(result.warnings)||result.warnings.some(item=>typeof item!=='string')))throw new Error('Некорректный формат предупреждений.');
      for(const key of ['deficiencyProbabilities','anemiaProbabilities'])if(result[key]!=null&&(!Array.isArray(result[key])||result[key].some(item=>typeof item.label!=='string'||!Number.isFinite(item.probability)||item.probability<0||item.probability>1)))throw new Error('Сервис вернул некорректные оценки модели.');
      for(const key of ['deficiencyScores','anemiaScores'])if(result[key]!=null&&(!Array.isArray(result[key])||result[key].some(item=>typeof item.label!=='string'||!Number.isFinite(item.score)||item.score<0||item.score>1)))throw new Error('Сервис вернул некорректные оценки модели.');
      validateFerritin(result.ferritinScreening);
      const phrases=await post(settings.paths.recommendations,{reportId:result.reportId,audience:role},signal);
      if(!Array.isArray(phrases.items)||phrases.items.some(item=>typeof item.text!=='string'))throw new Error('Некорректный формат рекомендаций.');
      if(phrases.warnings!=null&&(!Array.isArray(phrases.warnings)||phrases.warnings.some(item=>typeof item!=='string')))throw new Error('Некорректный формат предупреждений рекомендаций.');
      for(const list of [phrases.conclusions,phrases.insufficientData?.items])if(list!=null&&(!Array.isArray(list)||list.some(item=>typeof item.text!=='string')))throw new Error('Некорректный формат пояснений и полноты данных.');
      if(phrases.dataSufficiency!=null&&(typeof phrases.dataSufficiency.summary!=='string'||!['insufficient','limited','broader'].includes(phrases.dataSufficiency.level)))throw new Error('Некорректная оценка полноты данных.');
      const modelConnected=result.modelConnected??Boolean(result.modelVersion||result.prediction);
      return {...rule,...result,hemoglobin:rule.hemoglobin,threshold:rule.threshold,missing:rule.missing,inputs:rule.inputs,modelConnected,audience:role,source:modelConnected?'api':'api-rule',warnings:[...new Set([...(result.warnings||[]),...(phrases.warnings||[])])],recommendations:phrases.items,recommendationVersion:phrases.phraseFileVersion,recommendationStatus:phrases.status,recommendationConclusions:phrases.conclusions??[],dataSufficiency:phrases.dataSufficiency??result.dataSufficiency,insufficientData:phrases.insufficientData??null,clinicalReviewStatus:phrases.clinicalReviewStatus};
    },
    async extractPDF(files,{signal,audience='patient'}={}) {
      if(!roles.includes(audience))throw new Error('Неизвестная роль.');
      if(settings.mode!=='api')throw new Error('PDF добавлены в пакет. Распознавание ещё не подключено: для расчёта используйте ручной ввод или CSV/XLSX.');
      const body=new FormData();for(const file of files)body.append('files',file);
      body.append('audience',audience);
      const result=await post(settings.paths.pdf,body,signal);
      if(!result.inputs||typeof result.observationId!=='string'||!Number.isInteger(result.revision))throw new Error('Неверный формат распознанного наблюдения.');
      revisions.set(result.observationId,result.revision);
      return result;
    },
    async clearSession(){
      revisions.clear();
      if(settings.mode!=='api')return {deleted:true};
      const response=await request(settings.baseUrl+'/api/privacy/session',{method:'DELETE',credentials:'include',headers:{'X-Hema-Client':'1'}});
      if(!response.ok)throw new Error('Не удалось удалить данные на сервере. Повторите очистку.');
      return response.json();
    }
  };
}
export const client=createClient();
