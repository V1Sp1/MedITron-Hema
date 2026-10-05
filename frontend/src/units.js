// Units confirmed by variables.xlsx, Variables!A2:C49.
// Canonical metadata: data/feature_dictionary.json at the project root.
export const caseUnits = {
  age_years:'years', hemoglobin:'g/L', RBC:'10^12/L', hematocrit:'%', MCV:'fL',
  MCH:'pg', MCHC:'g/L', RDW:'%', platelets:'10^9/L', WBC:'10^9/L',
  reticulocytes:'%', ferritin:'µg/L', serum_iron:'µmol/L', transferrin:'g/L',
  TIBC:'µmol/L', UIBC:'µmol/L', TSAT:'%', sTfR:'mg/L', Ret_He:'pg',
  vitamin_B12:'pg/mL', active_B12:'pmol/L', MMA:'µmol/L', homocysteine:'µmol/L',
  folate:'ng/mL', vitamin_B6:'nmol/L', copper:'µmol/L', ceruloplasmin:'g/L',
  CRP:'mg/L', ESR:'mm/h', creatinine:'µmol/L', eGFR:'mL/min/1.73m²', TSH:'mIU/L',
  albumin:'g/L', LDH:'U/L', indirect_bilirubin:'µmol/L', haptoglobin:'g/L'
};
const labels = {
  'years':'лет', 'g/L':'г/л', '10^12/L':'×10¹²/л', '%':'%', 'fL':'фл', 'pg':'пг',
  '10^9/L':'×10⁹/л', 'µg/L':'мкг/л', 'µmol/L':'мкмоль/л', 'mg/L':'мг/л',
  'pg/mL':'пг/мл', 'pmol/L':'пмоль/л', 'ng/mL':'нг/мл', 'nmol/L':'нмоль/л',
  'mm/h':'мм/ч', 'mL/min/1.73m²':'мл/мин/1,73 м²', 'mIU/L':'мМЕ/л', 'U/L':'Ед/л'
};
export const unitLabels = Object.fromEntries(Object.entries(caseUnits).map(([key,unit])=>[key,labels[unit]]));
