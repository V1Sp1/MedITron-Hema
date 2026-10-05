import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve,extname,sep} from 'node:path';
const root=fileURLToPath(new URL('../',import.meta.url));
const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.json':'application/json','.woff2':'font/woff2'};
createServer(async(req,res)=>{
  try{
    const url=new URL(req.url,'http://localhost');const path=resolve(root,'.'+decodeURIComponent(url.pathname==='/'?'/index.html':url.pathname));
    if(!path.startsWith(root.endsWith(sep)?root:root+sep)){res.writeHead(403).end();return;}
    const relative=path.slice(root.length).replace(/^\//,'');
    if(!['index.html','patient.html','doctor.html','favicon.svg','tests/xlsx.html'].includes(relative)&&!relative.startsWith('src/')&&!relative.startsWith('assets/')){res.writeHead(404).end('Not found');return;}
    const body=await readFile(path);res.writeHead(200,{'Content-Type':mime[extname(path)]||'application/octet-stream','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(body);
  }catch{res.writeHead(404).end('Not found');}
}).listen(5173,'127.0.0.1',()=>console.log('Hema: http://127.0.0.1:5173'));
