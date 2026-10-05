// A biochemical marker forecast is separate from the clinical class and phrases.
const statuses=['observed','research_prediction','outside_scope','unknown_pregnancy','insufficient_data','unavailable','disabled'];

export function validateFerritin(value){
  if(value==null)return; // Older saved reports remain readable.
  if(typeof value!=='object'||value.endpoint!=='low_ferritin'||!statuses.includes(value.status)||typeof value.message!=='string'||value.clinicalValidated!==false)
    throw new Error('Некорректный формат оценки ферритина.');
  if(value.status==='observed'&&(!Number.isFinite(value.observedValue)||value.observedValue<0||value.score!=null))
    throw new Error('Некорректный результат анализа ферритина.');
  if(value.status==='research_prediction'&&(!Number.isFinite(value.score)||value.score<0||value.score>1||!Number.isFinite(value.decisionThreshold)||value.decisionThreshold<0||value.decisionThreshold>1.000000000000001||typeof value.screenPositive!=='boolean'||value.screenPositive!==(value.score>=value.decisionThreshold)||!['cbc','extended'].includes(value.route)||value.markerUsedAsPredictor!==false||value.externalGatePassed!==true||value.scoreMeaning!=='research_score_for_low_biochemical_marker'))
    throw new Error('Некорректный прогноз ферритина.');
  if(!['observed','research_prediction'].includes(value.status)&&value.score!=null)
    throw new Error('Оценка ферритина недоступна, числовой прогноз не допускается.');
}

export function ferritinDetails(report){
  const value=report.ferritinScreening;
  if(!value)return null;
  const paragraphs=[value.message];
  if(value.status==='observed')paragraphs.unshift(`Измеренный ферритин: ${value.observedValue} ${value.unit}.`);
  if(value.status==='research_prediction'){
    if(report.audience==='doctor')paragraphs.push(`Оценка модели: ${value.score.toFixed(3)}. Исследовательский порог: ${value.decisionThreshold.toFixed(3)}. Это не вероятность заболевания.`);
    paragraphs.push('Модель прогнозирует ферритин ниже 15 µg/L. Проверенная группа: женщины 18–49 лет; беременность исключалась.');
    paragraphs.push(value.limitation);
  }
  return {title:value.status==='observed'?'Ферритин: результат анализа':'Прогноз низкого ферритина',paragraphs:paragraphs.filter(Boolean)};
}

export function ferritinDetailsHTML(report,e){
  const details=ferritinDetails(report);
  if(!details)return '';
  return `<section class="result-section ferritin-section"><div class="result-section-heading"><h3>${e(details.title)}</h3></div>${details.paragraphs.map(text=>`<p class="helper">${e(text)}</p>`).join('')}</section>`;
}
