import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {createReportPDF} from '../src/pdf-report.js';
import {demoReport} from '../src/demo.js';
function load(name){const module={exports:{}};new Function('module','exports',readFileSync(new URL('../src/vendor/'+name,import.meta.url),'utf8'))(module,module.exports);return module.exports;}
const deps={PDFLib:load('pdf-lib.min.js'),fontkit:load('fontkit.min.js'),fontBytes:readFileSync(new URL('../src/assets/fonts/IBMPlexSans-Regular.ttf',import.meta.url))};
const out=new URL('../preview/pdf/',import.meta.url);mkdirSync(out,{recursive:true});
const patient={...demoReport('patient'),createdAt:'2026-10-03T15:00:00Z'};
const doctor={...demoReport('doctor'),createdAt:patient.createdAt};
const local={...patient,source:'local-rule',prediction:null,modelConnected:false,anemia:false,hemoglobin:135,recommendations:[],deficiencyProbabilities:[],anemiaProbabilities:[],warnings:[],recommendationVersion:null};
const long={...doctor,recommendations:Array.from({length:30},(_,i)=>({text:`${i+1}. Проверка переноса длинных рекомендаций. `+'Текст для обсуждения результатов с врачом и проверки доступности дополнительных анализов. '.repeat(4)})),warnings:['Длинный идентификатор: '+'a'.repeat(800)]};
for(const [name,report] of [['patient',patient],['doctor',doctor],['local',local],['long',long]]){
 const bytes=await createReportPDF(report,deps);writeFileSync(new URL(name+'.pdf',out),bytes);
 const doc=await deps.PDFLib.PDFDocument.load(bytes);console.log(name,bytes.length+' bytes',doc.getPageCount()+' pages');
}
