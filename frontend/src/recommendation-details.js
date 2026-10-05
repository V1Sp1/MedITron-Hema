import {escapeHTML as e} from './export.js';

export function recommendationDetails(report){
  const conclusions=report.recommendationConclusions??[];
  const data=report.dataSufficiency;
  const gaps=report.insufficientData?.items??[];
  const conclusion=conclusions.length?`<section class="result-section" aria-label="Пояснение результата"><div class="result-section-heading"><h3>Что удалось оценить</h3></div>${conclusions.map(item=>`<p class="helper">${e(item.text)}</p>`).join('')}</section>`:'';
  const coverage=data?`<section class="result-section" aria-label="Полнота данных"><div class="result-section-heading"><h3>${data.level==='insufficient'?'Недостаточно данных':'Полнота данных'}</h3></div><p class="helper">${e(data.summary)}</p>${gaps.length?'<ul class="helper">'+gaps.map(item=>'<li>'+e(item.text)+'</li>').join('')+'</ul>':''}</section>`:'';
  return conclusion+coverage;
}
