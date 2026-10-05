import {demoNotice,reportInputRows,scoreAvailabilityMessage} from './report-details.js';
import {ferritinDetails} from './ferritin.js';

// Local, searchable PDF. No report data is sent to an export service.
export async function createReportPDF(report,{PDFLib=globalThis.PDFLib,fontkit=globalThis.fontkit,fontBytes}={}){
  if(!PDFLib||!fontkit)throw new Error('Не удалось загрузить модуль PDF. Обновите страницу.');
  if(!fontBytes){
    const response=await fetch(new URL('./assets/fonts/IBMPlexSans-Regular.ttf',import.meta.url));
    if(!response.ok)throw new Error('Шрифт PDF недоступен. Повторите сохранение.');
    fontBytes=await response.arrayBuffer();
  }
  const {PDFDocument,rgb}=PDFLib;
  const pdf=await PDFDocument.create();pdf.registerFontkit(fontkit);
  const font=await pdf.embedFont(fontBytes,{subset:true});
  pdf.setTitle('Hema - информационный отчёт');pdf.setAuthor('NeuroNiXxx');
  const ink=rgb(.063,.212,.282),muted=rgb(.34,.44,.45),teal=rgb(.15,.48,.42);
  const margin=46,width=595.28,height=841.89,usable=width-2*margin;
  const supported=new Set(font.getCharacterSet());
  const clean=value=>Array.from(String(value??'')).map(c=>supported.has(c.codePointAt(0))?c:'?').join('').replace(/\r/g,'');
  let page,y;
  function newPage(){
    page=pdf.addPage([width,height]);y=height-margin;
    page.drawText('Hema.',{x:margin,y:y-22,size:25,font,color:ink});
    page.drawText('by NeuroNiXxx / '+(report.audience==='doctor'?'Для врача':'Для пациента'),{x:margin+105,y:y-18,size:10,font,color:muted});
    y-=43;page.drawLine({start:{x:margin,y},end:{x:width-margin,y},thickness:1.5,color:teal});y-=24;
  }
  function ensure(space){if(y-space<margin+25)newPage();}
  function lines(text,size){
    const output=[];
    for(const paragraph of clean(text).split('\n')){
      let line='';
      for(const word of paragraph.split(/\s+/)){
        if(!word)continue;
        if(font.widthOfTextAtSize((line?line+' ':'')+word,size)<=usable){line+=(line?' ':'')+word;continue;}
        if(line){output.push(line);line='';}
        for(const letter of word){
          if(font.widthOfTextAtSize(line+letter,size)>usable){output.push(line);line='';}
          line+=letter;
        }
      }
      output.push(line);
    }
    return output;
  }
  function text(value,{size=11,color=ink,gap=9}={}){
    for(const line of lines(value,size)){ensure(size*1.5);page.drawText(line,{x:margin,y:y-size,size,font,color});y-=size*1.5;}
    y-=gap;
  }
  function section(title){ensure(60);y-=9;text(title,{size:15,color:teal,gap:10});}
  newPage();
  text('Информационный отчёт',{size:21,gap:8});
  text(new Date(report.createdAt??Date.now()).toLocaleString('ru-RU'),{size:9,color:muted});
  text('Поддержка принятия решений. Hema не устанавливает диагноз и не назначает лечение. Результат требует оценки врачом.',{color:teal});
  text(report.source==='demo'?'ДЕМОНСТРАЦИЯ. '+demoNotice(report):!report.modelConnected?'Оценка по правилу гемоглобина. Модель дефицитов и причин анемии не подключена.':'Исследовательские оценки 0–1, не вероятности заболевания. Модель не откалибрована. Клиническое применение не разрешено.',{size:10,color:muted});
  section('Оценка по гемоглобину');
  text(report.anemia?'Гемоглобин ниже порога анемии.':'Гемоглобин не ниже порога анемии.');
  text(`Hb: ${report.hemoglobin} г/л. Порог: ${report.threshold} г/л. Отсутствие анемии по Hb не исключает дефициты.`,{size:10});
  section('Предполагаемое состояние');
  text(report.prediction?.label??'Причина состояния не оценена.');
  text(report.prediction?.hiddenDeficitLabel??'Скрытые дефициты не оценены.');
  const ferritin=ferritinDetails(report);
  if(ferritin){section(ferritin.title);for(const paragraph of ferritin.paragraphs)text(paragraph,{size:10});}
  if(report.recommendationConclusions?.length){
    section('Что удалось оценить');
    for(const item of report.recommendationConclusions)text(item.text,{size:10});
  }
  if(report.dataSufficiency){
    section(report.dataSufficiency.level==='insufficient'?'Недостаточно данных':'Полнота данных');
    text(report.dataSufficiency.summary,{size:10});
    for(const item of report.insufficientData?.items??[])text(item.text,{size:10});
  }
  if(report.audience==='doctor'){
    const groups=[['Оценки модели по дефицитам',(report.deficiencyScores??report.deficiencyProbabilities)],...(report.anemia?[['Оценки модели по видам анемии',(report.anemiaScores??report.anemiaProbabilities)]]:[])];
    for(const [title,items] of groups){
      section(title);
      if(!items?.length)text(scoreAvailabilityMessage(report),{size:10,color:muted});
      else for(const item of items)text(`${item.label}: ${Number(item.score??item.probability).toFixed(3)}`,{size:11,gap:3});
    }
  }
  section('Следующие шаги');
  if(report.recommendations?.length)report.recommendations.forEach((item,i)=>text(`${i+1}. ${item.text}`));
  else text('Проверенные рекомендации пока недоступны. Обсудите результаты с врачом.',{size:10});
  if(report.warnings?.length){section('Ограничения оценки');for(const warning of report.warnings)text(warning,{size:10,color:muted});}
  if(report.inputs){
    section('Исходные показатели');
    text(`Возраст: ${report.inputs.age_years}. Пол: ${report.inputs.sex==='F'?'женский':'мужской'}. Пустой показатель не считается нормальным.`,{size:10});
    for(const row of reportInputRows(report))text(`${row.label}: ${row.value===null?'не указан':row.value+' '+row.unit}`,{size:10,gap:3});
  }
  section('Происхождение отчёта');
  text(`Модель: ${report.modelVersion??'не подключена'}. ${report.source==='demo'?'Пример: '+report.demoScenarioLabel+'.':''}`,{size:9,color:muted});
  if(report.ferritinScreening?.status==='research_prediction')text(`Модель ферритина: ${report.ferritinScreening.modelVersion}. Панель: ${report.ferritinScreening.route}.`,{size:9,color:muted});
  text(`Версия файла фраз: ${report.recommendationVersion??'не подключён'}. ID отчёта: ${report.reportId??'локальный'}.`,{size:9,color:muted});
  const pages=pdf.getPages();pages.forEach((p,i)=>{
    p.drawText(`Hema / Поддержка принятия решений / ${i+1} из ${pages.length}`,{x:margin,y:25,size:8,font,color:muted});
  });
  return pdf.save();
}
