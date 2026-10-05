import {scenarios} from './demo-scenarios.js';

export const demoScenarios = scenarios.map(({id,label})=>({id,label}));

export function demoReport(role,scenarioId='iron'){
  if(!['patient','doctor'].includes(role))throw new Error('Неизвестная роль примера.');
  const scenario=scenarios.find(item=>item.id===scenarioId);
  if(!scenario)throw new Error('Неизвестный пример.');
  return structuredClone(scenario.reports[role]);
}
