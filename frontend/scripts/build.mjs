import {mkdir,cp} from 'node:fs/promises';
const root=new URL('../',import.meta.url);const out=new URL('dist/',root);
await mkdir(out,{recursive:true});
for(const path of ['index.html','patient.html','doctor.html','favicon.svg','src']) await cp(new URL(path,root),new URL(path,out),{recursive:true});
console.log('Built Hema: frontend/dist — home, patient, doctor.');
