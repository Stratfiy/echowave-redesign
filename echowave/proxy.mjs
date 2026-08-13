import http from 'node:http';
const route=(p)=>(p.startsWith('/api/v1')?8000:3014);
http.createServer((q,s)=>{const u=http.request({host:'127.0.0.1',port:route(q.url),path:q.url,method:q.method,headers:q.headers},(up)=>{s.writeHead(up.statusCode,up.headers);up.pipe(s);});u.on('error',()=>{s.writeHead(502);s.end('e');});q.pipe(u);}).listen(3013);
