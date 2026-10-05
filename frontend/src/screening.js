import {inputKeys} from './data.js';
export function numeric(value) {
  if (value == null || String(value).trim() === '' || /^(nan|null|na|n\/a)$/i.test(String(value).trim())) return null;
  const number = Number(String(value).trim().replace(',', '.'));
  return Number.isFinite(number) ? number : NaN;
}
export function validate(values) {
  const errors = [];
  if (!['F','M'].includes(values.sex)) errors.push('Укажите пол F или M для применения порога гемоглобина.');
  const age = numeric(values.age_years);
  if (age === null || !Number.isInteger(age) || age < 18 || age > 120) errors.push('Укажите целый возраст от 18 до 120 лет. Прототип предназначен для взрослых.');
  if (numeric(values.hemoglobin) === null) errors.push('Укажите гемоглобин в г/л.');
  for (const key of inputKeys.filter(key=>!['sex','age_years'].includes(key))) {
    const n = numeric(values[key]);
    if (Number.isNaN(n) || (n !== null && n < 0)) errors.push(`${key}: ожидается неотрицательное число или пустое поле.`);
  }
  return errors;
}
export function screen(values) {
  const errors = validate(values);
  if (errors.length) throw new Error(errors.join('\n'));
  const inputs = Object.fromEntries(inputKeys.map(key=>[key,key === 'sex' ? values[key] : numeric(values[key])]));
  const threshold = inputs.sex === 'F' ? 120 : 130;
  return {inputs,hemoglobin:inputs.hemoglobin,threshold,anemia:inputs.hemoglobin < threshold,deficiencies:null,modelConnected:false,missing:inputKeys.filter(key=>inputs[key] === null),method:'Правило порога Hb из кейса СУ; модель дефицитов не подключена'};
}
export function parseCSV(text) {
  text = text.replace(/^\uFEFF/,'');
  const firstLine = text.split(/\r?\n/)[0];
  const delimiter = (firstLine.match(/;/g)||[]).length > (firstLine.match(/,/g)||[]).length ? ';' : ',';
  const rows=[]; let row=[],cell='',quoted=false;
  for(let i=0;i<text.length;i++) {
    const c=text[i];
    if(c==='"') { if(quoted && text[i+1]==='"') {cell+='"';i++;} else if(!quoted && cell.trim()!=='') throw new Error('Некорректные кавычки в CSV.'); else quoted=!quoted; }
    else if(c===delimiter && !quoted) { row.push(cell.trim()); cell=''; }
    else if((c==='\n'||c==='\r') && !quoted) { if(c==='\r' && text[i+1]==='\n') i++; row.push(cell.trim()); if(row.some(Boolean)) rows.push(row);row=[];cell=''; }
    else cell+=c;
  }
  if(quoted) throw new Error('В CSV обнаружена незакрытая кавычка.');
  row.push(cell.trim()); if(row.some(Boolean)) rows.push(row);
  if(rows.length<2) throw new Error('CSV должен содержать заголовок и хотя бы одну строку данных.');
  const headers=rows.shift();
  if(new Set(headers).size!==headers.length) throw new Error('В CSV повторяются названия столбцов.');
  for(const required of ['age_years','sex','hemoglobin']) if(!headers.includes(required)) throw new Error(`В CSV отсутствует столбец ${required}.`);
  if(rows.length>10000) throw new Error('Максимум 10 000 строк на файл.');
  return rows.map((cells,index)=> {
    if(cells.length!==headers.length) throw new Error(`Строка ${index+2}: число значений не совпадает с заголовком.`);
    // Explicit whitelist prevents leakage from all target labels and excludes patient identifiers.
    return Object.fromEntries(inputKeys.map(key=>[key,headers.includes(key)?cells[headers.indexOf(key)]:'']));
  });
}
