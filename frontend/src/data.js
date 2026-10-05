import {unitLabels} from './units.js';
export const fields = [
  ['hemoglobin', 'Гемоглобин', 'г/л', 'core'], ['RBC', 'Эритроциты', '×10¹²/л', 'core'],
  ['MCV', 'Объём эритроцита', 'фл', 'core'], ['MCH', 'Гемоглобин в эритроците', 'пг', 'core'],
  ['hematocrit', 'Гематокрит', '%', 'core'], ['RDW', 'Распределение эритроцитов', '%', 'core'],
  ['ferritin', 'Ферритин', 'мкг/л', 'extra'], ['serum_iron', 'Сывороточное железо', 'мкмоль/л', 'extra'],
  ['vitamin_B12', 'Витамин B₁₂', 'пг/мл', 'extra'], ['folate', 'Фолаты', 'нг/мл', 'extra'],
  ...[['MCHC','Концентрация Hb'],['platelets','Тромбоциты'],['WBC','Лейкоциты'],['reticulocytes','Ретикулоциты'],['transferrin','Трансферрин'],['TIBC','ОЖСС'],['UIBC','НЖСС'],['TSAT','Насыщение трансферрина'],['sTfR','Растворимый рецептор трансферрина'],['Ret_He','Hb ретикулоцитов'],['active_B12','Активный B₁₂'],['MMA','Метилмалоновая кислота'],['homocysteine','Гомоцистеин'],['vitamin_B6','Витамин B₆'],['copper','Медь'],['ceruloplasmin','Церулоплазмин'],['CRP','С-реактивный белок'],['ESR','СОЭ'],['creatinine','Креатинин'],['eGFR','СКФ'],['TSH','ТТГ'],['albumin','Альбумин'],['LDH','ЛДГ'],['indirect_bilirubin','Непрямой билирубин'],['haptoglobin','Гаптоглобин']].map(([key,label])=>[key,label,unitLabels[key],'extended'])
].map(([key,label,,group])=>[key,label,unitLabels[key],group]);
export const demo = {age_years:42,sex:'F',hemoglobin:108,RBC:3.9,MCV:76,MCH:25,hematocrit:33,RDW:16.2,ferritin:9,serum_iron:5.8,vitamin_B12:340,folate:8.2};
export const inputKeys = ['age_years','sex',...fields.map(([key])=>key)];
export const groups = [
  {id:'blood',label:'Кровь',name:'Общий анализ крови',keys:['hemoglobin','RBC','hematocrit','MCV','MCH','MCHC','RDW','platelets','WBC','reticulocytes']},
  {id:'iron',label:'Железо',name:'Обмен железа',keys:['ferritin','serum_iron','transferrin','TIBC','UIBC','TSAT','sTfR','Ret_He']},
  {id:'vitamins',label:'Витамины',name:'Витамины и их маркеры',keys:['vitamin_B12','active_B12','MMA','homocysteine','folate','vitamin_B6']},
  {id:'minerals',label:'Микроэлементы',name:'Медь и церулоплазмин',keys:['copper','ceruloplasmin']},
  {id:'inflammation',label:'Воспаление',name:'Маркеры воспаления',keys:['CRP','ESR']},
  {id:'metabolism',label:'Почки и обмен',name:'Почки, щитовидная железа и белковый обмен',keys:['creatinine','eGFR','TSH','albumin']},
  {id:'hemolysis',label:'Гемолиз',name:'Маркеры гемолиза',keys:['LDH','indirect_bilirubin','haptoglobin']}
];
