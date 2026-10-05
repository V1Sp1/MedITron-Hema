import {fields} from './data.js';

export function demoNotice(report){
  return report.demoModelComputed
    ?'Вымышленные анализы. Показан сохранённый расчёт локальной модели и рекомендации для выбранной роли.'
    :'Вымышленные данные для демонстрации интерфейса.';
}

export function scoreAvailabilityMessage(report){
  if(report.modelDecisionState==='suppressed_sparse'||report.dataSufficiency?.level==='insufficient')
    return 'Оценки не выводятся: слишком мало анализов для оценки причины. Доступно правило гемоглобина.';
  if(!report.modelConnected)return 'Оценки недоступны: модель не подключена.';
  if(!report.anemia)return 'Оценки по видам анемии не выводятся: гемоглобин не ниже порога.';
  return 'Числовые оценки отсутствуют в этом отчёте.';
}

export function reportInputRows(report){
  const inputs=report.inputs??{};
  return fields.map(([key,label,unit])=>({key,label,unit,value:inputs[key]??null}));
}

const states={evaluated:'Предположение сформировано',uncertain:'Причина не определена',
  inconsistent:'Выводы модели расходятся',suppressed_sparse:'Мало данных для оценки причины'};

export function inputDetailsHTML(report,e){
  if(!report.inputs)return '';
  const inputs=report.inputs;
  const rows=reportInputRows(report);
  const count=rows.filter(row=>row.value!==null).length;
  return `<details class="input-details"><summary>Показатели ${report.source==='demo'?'примера':'расчёта'} · ${count}/35</summary><p class="helper">Возраст: ${e(inputs.age_years??'не указан')}. Пол: ${inputs.sex==='F'?'женский':inputs.sex==='M'?'мужской':'не указан'}. Пустой показатель не считается нормальным.</p><table class="report-input-table"><thead><tr><th scope="col">Показатель</th><th scope="col">Значение</th><th scope="col">Единица</th></tr></thead><tbody>${rows.map(row=>`<tr><th scope="row">${e(row.label)}</th><td>${e(row.value??'не указан')}</td><td>${e(row.unit)}</td></tr>`).join('')}</tbody></table><p class="helper">${e(states[report.modelDecisionState]??'Расчёт по правилу Hb')}. Модель: ${e(report.modelVersion??'не подключена')}. Фразы: ${e(report.recommendationVersion??'не подключены')}.</p></details>`;
}
