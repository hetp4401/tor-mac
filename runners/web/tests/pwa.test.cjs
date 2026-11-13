const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const sw = fs.readFileSync(path.join(__dirname, '../pwa/sw.js'),'utf8');
const client = fs.readFileSync(path.join(__dirname, '../pwa/client.js'),'utf8');
test('Frame rebranding preserves the installed app identity and refreshes its icons',()=>{
  const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'../pwa/manifest.webmanifest'),'utf8'));
  assert.equal(manifest.name,'Frame');assert.equal(manifest.short_name,'Frame');
  assert.equal(manifest.id,'/watch');assert.equal(manifest.start_url,'/watch');assert.equal(manifest.scope,'/');
  assert.ok(manifest.icons.every(icon=>icon.src.endsWith('?v=4')));
});
function worker() {
  const listeners={}, storage=new Map(), calls=[]; let skip=0, claim=0;
  const key = x => new URL(typeof x==='string'?x:x.url,'https://fleet.test').href;
  const cache = name => {
    if (!storage.has(name)) storage.set(name,new Map());
    const map=storage.get(name);
    return {match:async req=>map.get(key(req))?.clone(),put:async(req,res)=>map.set(key(req),res),keys:async()=>Array.from(map.keys()).map(x=>new Request(x)),delete:async req=>map.delete(key(req))};
  };
  let fetcher=async req=>new Response('<html>fresh</html>',{headers:{'Content-Type':'text/html'}});
  const context=vm.createContext({URL,Request,Response,AbortController,setTimeout,clearTimeout,console,
    self:{location:{origin:'https://fleet.test'},addEventListener:(kind,fn)=>listeners[kind]=fn,skipWaiting:async()=>skip++,clients:{claim:async()=>claim++}},
    caches:{open:async name=>cache(name),keys:async()=>Array.from(storage.keys()),delete:async name=>storage.delete(name)},
    fetch:async(...args)=>{calls.push(args);return fetcher(...args);}});
  vm.runInContext(sw+'\nglobalThis.config={PAGE_CACHE,ASSET_CACHE,ASSET_LIMIT};',context);
  const fire=async(kind,args={})=>{const pending=[];let response=null;listeners[kind]({...args,waitUntil:p=>pending.push(p),respondWith:p=>response=p});const result=response?await response:null;await Promise.all(pending);return result;};
  return {fire,cache,storage,calls,config:context.config,setFetch:f=>fetcher=f,get skip(){return skip;},get claim(){return claim;}};
}
test('worker never intercepts media, APIs, signed URLs, external requests, or byte ranges', async()=>{
  const w=worker();
  for(const url of ['/hls/secret/index.m3u8','/file/secret/movie.mkv','/browser/facts?k=x','/api/private','/mac-source/secret','https://elsewhere.test/watch']){
    assert.equal(await w.fire('fetch',{request:new Request(new URL(url,'https://fleet.test'))}),null,url);
  }
  assert.equal(await w.fire('fetch',{request:new Request('https://fleet.test/watch',{headers:{Range:'bytes=0-1'}})}),null);
  assert.equal(w.calls.length,0);
});
test('offline shell falls back to its cached copy and never substitutes a failed response',async()=>{
  const w=worker(), request=new Request('https://fleet.test/watch');
  assert.equal(await (await w.fire('fetch',{request})).text(),'<html>fresh</html>');
  w.setFetch(async()=>{throw Error('offline');});
  assert.equal(await (await w.fire('fetch',{request})).text(),'<html>fresh</html>');
  w.setFetch(async()=>new Response('bad gateway',{status:503}));
  assert.equal(await (await w.fire('fetch',{request})).text(),'<html>fresh</html>');
});
test('first offline visit has a useful recovery document',async()=>{
  const w=worker();w.setFetch(async()=>{throw Error('offline');});
  const r=await w.fire('fetch',{request:new Request('https://fleet.test/watch')});
  const html=await r.text();
  assert.equal(r.status,200); assert.match(html,/You’re offline/);assert.match(html,/<title>Frame · Offline<\/title>/);
});
test('activation removes only app-owned old caches, including the Fleet lineage',async()=>{
  const w=worker();
  for(const name of ['fleet-1','fleet-shell-old','fleet-assets-old','unrelated-app','user-downloads',w.config.PAGE_CACHE,w.config.ASSET_CACHE])w.cache(name);
  await w.fire('activate');
  assert.deepEqual(Array.from(w.storage.keys()).sort(),[w.config.PAGE_CACHE,w.config.ASSET_CACHE,'unrelated-app','user-downloads'].sort());
  assert.equal(w.claim,1);
});
test('an update with failed precaching retains the previous offline shell',async()=>{
  const w=worker();
  await w.cache('fleet-shell-old').put('/watch',new Response('<html>previous shell</html>'));
  w.setFetch(async()=>{throw Error('offline');});
  await w.fire('install');await w.fire('activate');
  assert.equal(await (await w.cache(w.config.PAGE_CACHE).match('/watch')).text(),'<html>previous shell</html>');
  assert.equal(w.storage.has('fleet-shell-old'),false);
});
test('installation waits for explicit activation and asset cache stays bounded',async()=>{
  const w=worker(); await w.fire('install');assert.equal(w.skip,0);
  await w.fire('message',{data:{type:'FLEET_ACTIVATE_UPDATE'}});assert.equal(w.skip,1);
  for(let i=0;i<20;i++)await w.fire('fetch',{request:new Request('https://fleet.test/pwa/icon-192.png?v='+i)});
  assert.equal((await w.cache(w.config.ASSET_CACHE).keys()).length,w.config.ASSET_LIMIT);
  assert.ok(await w.cache(w.config.PAGE_CACHE).match('/watch'));
});
test('pinned hls library is cached but arbitrary CDN paths are never retained',async()=>{
  const w=worker();w.setFetch(async()=>new Response('library',{headers:{'Content-Type':'application/javascript'}}));
  const request=new Request('https://fleet.test/gw/cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js');
  await w.fire('fetch',{request});await w.fire('fetch',{request});assert.equal(w.calls.length,1);
  assert.equal(await w.fire('fetch',{request:new Request('https://fleet.test/gw/cdn.jsdelivr.net/private-token')}),null);
});
function clientContext({playing=null,pip=false,secure=true,waiting=true}={}) {
  const winEvents={},swEvents={},videoEvents={},regEvents={}, elements={};let reloads=0,prompts=0;
  const control={postMessage:data=>control.messages.push(data),messages:[]};
  const reg={waiting:waiting?control:null,addEventListener:(type,fn)=>regEvents[type]=fn};
  const element=()=>({hidden:false,onclick:null,attributes:{},setAttribute(k,v){this.attributes[k]=v;},querySelector(q){return this.children[q]||null;},children:{},set innerHTML(value){this.html=value;this.children={'.pwa-close':element()};if(value.includes('class="pwa-action"'))this.children['.pwa-action']=element();},get innerHTML(){return this.html||'';}});
  elements.pwaInstall=element();elements.vid={paused:true,ended:false,webkitPresentationMode:pip?'picture-in-picture':'inline',addEventListener:(type,fn)=>videoEvents[type]=fn};
  let notice;
  const nav={vendor:'Apple Computer, Inc.',platform:'MacIntel',maxTouchPoints:0,userAgent:'Version/18 Safari/605',onLine:true,serviceWorker:{addEventListener:(type,fn)=>swEvents[type]=fn,register:async()=>reg}};
  const context=vm.createContext({console,navigator:nav,playing,byId:id=>elements[id],matchMedia:()=>({matches:false}),esc:s=>String(s),ICON:{close:'×'},queueMicrotask,
    setTimeout:()=>1,clearTimeout:()=>{},location:{reload:()=>reloads++},window:{isSecureContext:secure,addEventListener:(type,fn)=>winEvents[type]=fn},
    document:{pictureInPictureElement:null,body:{appendChild:node=>notice=node},createElement:()=>element(),addEventListener:()=>{}}});
  vm.runInContext(client,context);
  return {context,nav,elements,winEvents,swEvents,videoEvents,control,reg,get notice(){return notice;},get reloads(){return reloads;}};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('PWA never automatically reloads on registration or external controller changes',async()=>{
  const c=clientContext();await flush();c.swEvents.controllerchange();assert.equal(c.reloads,0);assert.equal(c.notice.hidden,false);
});
test('an approved update is deferred if playback starts before activation',async()=>{
  const c=clientContext();await flush();c.notice.querySelector('.pwa-action').onclick();
  assert.equal(c.control.messages.length,1);
  c.context.playing={url:'movie'};c.reg.waiting=null;c.swEvents.controllerchange();assert.equal(c.reloads,0);
  c.context.playing=null;c.winEvents.hashchange();await flush();
  assert.match(c.notice.innerHTML,/Ready to finish updating/);c.notice.querySelector('.pwa-action').onclick();assert.equal(c.reloads,1);
});
test('native Picture in Picture suppresses update UI without relying on current route',async()=>{
  const c=clientContext({pip:true});await flush();assert.equal(c.notice.hidden,true);assert.equal(c.reloads,0);
});
test('install prompt remains user initiated',async()=>{
  const c=clientContext({waiting:false});await flush();let prompts=0,prevented=0;
  c.winEvents.beforeinstallprompt({preventDefault:()=>prevented++,prompt:async()=>prompts++,userChoice:Promise.resolve({outcome:'dismissed'})});
  assert.equal(prevented,1);assert.equal(prompts,0);await c.elements.pwaInstall.onclick();assert.equal(prompts,1);
});
