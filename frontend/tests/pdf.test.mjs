import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createReportPDF} from '../src/pdf-report.js';
import {demoReport} from '../src/demo.js';
const load=name=>{const module={exports:{}};new Function('module','exports',readFileSync(new URL('../src/vendor/'+name,import.meta.url),'utf8'))(module,module.exports);return module.exports;};
const PDFLib=load('pdf-lib.min.js');
const deps={PDFLib,fontkit:load('fontkit.min.js'),fontBytes:readFileSync(new URL('../src/assets/fonts/IBMPlexSans-Regular.ttf',import.meta.url))};

test('Cyrillic reports create real A4 PDFs with an embedded font for both audiences',async()=>{
  for(const audience of ['patient','doctor']){
    const bytes=await createReportPDF(demoReport(audience),deps);
    assert.equal(Buffer.from(bytes.slice(0,5)).toString(),'%PDF-');
    const pdf=await PDFLib.PDFDocument.load(bytes);
    assert.equal(pdf.getAuthor(),'NeuroNiXxx');
    for(const page of pdf.getPages()){
      assert.equal(page.getWidth(),595.28);assert.equal(page.getHeight(),841.89);
      assert.ok(page.node.Resources().get(PDFLib.PDFName.of('Font')));
    }
  }
});
test('long recommendations and unbroken identifiers paginate without export failure',async()=>{
  const report={...demoReport('doctor'),recommendations:Array.from({length:40},()=>({text:'Рекомендации для обсуждения с врачом. '.repeat(8)})),warnings:['x'.repeat(1200)]};
  const bytes=await createReportPDF(report,deps);
  const pdf=await PDFLib.PDFDocument.load(bytes);
  assert.ok(pdf.getPageCount()>3);
});
