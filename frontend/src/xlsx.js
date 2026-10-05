import {parseCSV} from './screening.js';
// JSZip is vendored with its license. Only spreadsheet data is read; macros/formulas are not executed.
const xml=text=>{const doc=new DOMParser().parseFromString(text,'application/xml');if(doc.querySelector('parsererror'))throw new Error('Не удалось прочитать структуру XLSX.');return doc;};
const elements=(node,name)=>[...node.getElementsByTagNameNS('*',name)];
export async function parseXLSX(buffer){
  const zip=await globalThis.JSZip.loadAsync(buffer);
  let expanded=0;for(const file of Object.values(zip.files)){expanded+=file._data?.uncompressedSize||0;if(expanded>30*1024*1024)throw new Error('Распакованный XLSX превышает 30 МБ.');}
  async function read(path,optional=false){const file=zip.file(path);if(!file){if(optional)return '';throw new Error('В XLSX отсутствует обязательная часть: '+path);}return file.async('string');}
  const workbook=xml(await read('xl/workbook.xml'));
  const sheets=elements(workbook,'sheet').filter(sheet=>!['hidden','veryHidden'].includes(sheet.getAttribute('state')));
  if(!sheets.length)throw new Error('В XLSX нет видимых листов.');
  const relId=sheets[0].getAttributeNS('http://schemas.openxmlformats.org/officeDocument/2006/relationships','id');
  const rels=xml(await read('xl/_rels/workbook.xml.rels'));
  const relation=elements(rels,'Relationship').find(rel=>rel.getAttribute('Id')===relId);
  if(!relation||relation.getAttribute('TargetMode')==='External')throw new Error('Не найден первый лист XLSX.');
  const target=relation.getAttribute('Target');
  const parts=[];for(const part of (target.startsWith('/')?target.slice(1):'xl/'+target).split('/')){if(part==='..')parts.pop();else if(part!=='.')parts.push(part);}
  const sheet=xml(await read(parts.join('/')));
  const sharedText=await read('xl/sharedStrings.xml',true);
  const strings=sharedText?elements(xml(sharedText),'si').map(item=>elements(item,'t').map(t=>t.textContent).join('')):[];
  const rows=elements(sheet,'row');if(rows.length>10001)throw new Error('Максимум 10 000 записей в XLSX.');
  const matrix=rows.map(row=>{const cells=[];for(const cell of elements(row,'c')){const address=cell.getAttribute('r')||'';const letters=address.match(/^[A-Z]+/i)?.[0];if(!letters)throw new Error('Некорректный адрес ячейки XLSX.');let index=0;for(const c of letters.toUpperCase())index=index*26+c.charCodeAt(0)-64;index--;if(index>200)throw new Error('В XLSX поддерживается не более 201 столбца.');const type=cell.getAttribute('t');const value=elements(cell,'v')[0]?.textContent??'';if(elements(cell,'f').length&&!value)throw new Error('Пересчитайте формулы и сохраните XLSX перед загрузкой.');if(type==='e')throw new Error('В XLSX есть ячейка с ошибкой.');cells[index]=type==='s'?strings[Number(value)]??'':type==='inlineStr'?elements(cell,'t').map(t=>t.textContent).join(''):value;}return cells;}).filter(row=>row.some(value=>String(value??'').trim()));
  const width=matrix[0]?.length??0;
  const csv=matrix.map(row=>{if(row.length>width)throw new Error('В XLSX есть значения без названий столбцов.');return Array.from({length:width},(_,i)=>'"'+String(row[i]??'').replaceAll('"','""')+'"').join(',');}).join('\n');
  return {rows:parseCSV(csv),sheetName:sheets[0].getAttribute('name'),otherSheets:sheets.length-1};
}
