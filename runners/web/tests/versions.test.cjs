const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../versions.js'), 'utf8').split('const sheet =')[0];
function api(extra = {}) {
  const context = vm.createContext({console, ...extra});
  vm.runInContext(code + '\nglobalThis.api = {versionBytes, sortedVersions, fitVersions, applePlayer, verTop, versionPlayLabel, verRow, trapDialogTab, renderVersions, pauseVersionFeed, syncVersionFeeds};', context);
  return context.api;
}
const source = (size, mode='🎞 Original', url=size+mode) => ({name:mode+'\n4K HDR',title:'Movie.mkv\n💾 '+size+' · source',url});
test('file sizes are numeric across decimal and binary units', () => {
  const {versionBytes} = api();
  for (const [size, expected] of [['900 MB',9e8],['12.5 GB',12.5e9],['1 TB',1e12],['1 GiB',2**30],['1,024 MB',1024e6]]) {
    assert.equal(versionBytes(source(size)), expected, size);
  }
  assert.equal(versionBytes({...source('unknown'),behaviorHints:{videoSize:123456}}),123456);
});
test('zero, missing and invalid sizes remain unknown, never a zero-byte preference', () => {
  const {versionBytes} = api();
  for (const size of ['unknown','0.0 GB','not measured','', 'GB']) assert.equal(versionBytes(source(size)),null);
  assert.equal(versionBytes({title:'Movie.4GB.mkv'}),null);
});
test('one list orders sizes globally, preserving different playback types', () => {
  const {sortedVersions} = api();
  const rows=[source('900 MB','🌐 Browser 1080p'),source('20 GB'),source('3 GB','▶ As is'),source('20 GB','🌐 Browser 1080p'),source('unknown')];
  const before=rows.slice();
  const largest=Array.from(sortedVersions(rows,'largest'));
  assert.deepEqual(largest.map(x=>x.title.split('\n')[1]),[rows[1],rows[3],rows[2],rows[0],rows[4]].map(x=>x.title.split('\n')[1]));
  const smallest=Array.from(sortedVersions(rows,'smallest'));
  assert.equal(smallest[0],rows[0]); assert.equal(smallest.at(-1),rows[4]);
  assert.deepEqual(rows,before,'sort must not mutate API response order');
});
test('exact duplicate mode URLs collapse while original and converted choices survive', () => {
  const {sortedVersions} = api();
  const original=source('5 GB'), converted=source('5 GB','🌐 Browser 1080p');
  const rows=Array.from(sortedVersions([original,{...original},converted,{...converted,url:original.url}], 'largest'));
  assert.equal(rows.length,3);
  assert.equal(rows[0],original);
  assert.ok(rows.includes(converted));
});
test('unknown-size ordering is stable in either direction and invalid URLs are omitted', () => {
  const {sortedVersions} = api();
  const a=source('unknown','▶ As is','a'), b=source('unknown','🌐 Browser 1080p','b');
  for(const mode of ['largest','smallest']) assert.deepEqual(Array.from(sortedVersions([a,source('2 GB'),b,{...a,url:''}],mode)).slice(-2),[a,b]);
});
test('native HLS with Apple vendor or WebKit presentation APIs uses Apple player capability', () => {
  const native=()=>({canPlayType:()=> 'maybe'});
  assert.equal(api({navigator:{vendor:'Apple Computer, Inc.'},document:{createElement:native}}).applePlayer(),true);
  assert.equal(api({navigator:{vendor:'Google Inc.'},document:{createElement:()=>({...native(),webkitEnterFullscreen(){}})}}).applePlayer(),true);
  assert.equal(api({navigator:{vendor:'Google Inc.'},document:{createElement:native}}).applePlayer(),false);
  assert.equal(api({navigator:{vendor:'Apple Computer, Inc.'},document:{createElement:()=>({canPlayType:()=>''})}}).applePlayer(),false);
});
test('stale Direct and unsupported modes never become playback options', () => {
  const {fitVersions,sortedVersions} = api({store:{get:()=>({})}});
  const keep=[source('12 GB'),source('12 GB','▶ As is'),source('12 GB','🌐 Browser 1080p')];
  const stale=[source('50 GB','📄 Direct'),source('40 GB','External player'),source('30 GB','Unknown mode')];
  assert.deepEqual(Array.from(fitVersions([...keep,...stale])),keep);
  for(const order of ['largest','smallest']) {
    assert.deepEqual(Array.from(sortedVersions([...stale,...keep],order)),keep);
    assert.equal(sortedVersions(stale,order).length,0);
  }
});
test('each playback type starts synchronously and copy remains a separate action', () => {
  const copied=[], played=[];
  const {verRow,versionPlayLabel}=api({URL,location:{href:'https://frame.test/watch'},esc:s=>String(s),ICON:{check:'check',play:'play',link:'link',dl:'download'},
    navigator:{clipboard:{writeText:async url=>copied.push(url)}},toast:()=>{},
    document:{createElement:()=>({dataset:{},controls:{'.vplay':{},'.cp':{}},querySelector(q){return this.controls[q];}})}});
  for(const [mode,label] of [['🎞 Original','Original'],['▶ As is','As is'],['🌐 Browser 1080p','Converted']]) {
    const item={...source('12 GB',mode),download:'/dl/a/movie.mkv'};
    const row=verRow(item,s=>played.push(s));
    assert.ok(versionPlayLabel(item).startsWith('Play '+label+':'));
    row.querySelector('.vplay').onclick();
    assert.equal(played.at(-1),item,'playback keeps the original user activation');
    assert.equal(copied.length,0,'playing must not copy a raw-file link');
    assert.doesNotMatch(row.innerHTML,/External player|Copy link for an external/);
  }
  const item={...source('12 GB'),download:'/dl/a/movie.mkv'};
  verRow(item,s=>played.push(s)).querySelector('.cp').onclick();
  assert.deepEqual(copied,['https://frame.test/file/a/movie.mkv']);
  assert.equal(played.length,3,'the copy action must not start playback');
});
test('episode dialog keeps keyboard focus inside visible controls in both directions', () => {
  let focused=null, prevented=0;
  const node=(name,visible=true)=>({name,tabIndex:0,getClientRects:()=>visible?[{}]:[],focus(){focused=this;}});
  const first=node('close'), hidden=node('hidden',false), last=node('download');
  const document={activeElement:last};
  const dialog={querySelectorAll:()=>[first,hidden,last],contains:element=>[first,hidden,last].includes(element)};
  const {trapDialogTab}=api({document});
  trapDialogTab({key:'Tab',preventDefault:()=>prevented++},dialog);assert.equal(focused,first);
  document.activeElement=first;
  trapDialogTab({key:'Tab',shiftKey:true,preventDefault:()=>prevented++},dialog);assert.equal(focused,last);
  document.activeElement={};
  trapDialogTab({key:'Tab',preventDefault:()=>prevented++},dialog);assert.equal(focused,first);
  assert.equal(prevented,3);
});

// A small DOM harness exercises feed boundaries and observer lifecycle without
// network requests or timing-dependent scrolling.
function feedHarness({wide=true, automatic=true}={}) {
  const observers=[], focused=[];
  class Node {
    constructor(tag='div') { this.tag=tag;this.children=[];this.parent=null;this.style={};this.dataset={};this.listeners={};this.controls={};this.attrs={};this.className=''; }
    get isConnected() { return this.rootConnected || !!(this.parent && this.parent.isConnected); }
    appendChild(node) { if(node.tag==='fragment') { [...node.children].forEach(child=>this.appendChild(child));return node; } node.remove();node.parent=this;this.children.push(node);return node; }
    remove() { if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);this.parent=null; }
    replaceChildren() { [...this.children].forEach(n=>n.remove()); }
    setAttribute(k,v) { this.attrs[k]=v; }
    removeAttribute(k) { delete this.attrs[k]; }
    addEventListener(k,fn) { this.listeners[k]=fn; }
    getClientRects() { return this.visible===false?[]:[{}]; }
    closest(selector) { if(selector==='.ver')return this.className==='ver'?this:this.parent?.closest(selector);return null; }
    querySelector(selector) { return this.controls[selector] ||= Object.assign(new Node('button'),{parent:this}); }
    querySelectorAll(selector) { const rows=this.children.filter(n=>n.className==='ver');return selector==='.vplay'?rows.map(row=>row.querySelector('.vplay')):selector==='.ver'?rows:[]; }
    focus() { focused.push(this);document.activeElement=this; }
  }
  const box=new Node();box.rootConnected=true;
  Object.defineProperty(box,'innerHTML',{set(value){this.markup=value;this.replaceChildren();this.controls={};for(const selector of ['.vgroup','select','.vsummary']) { const node=new Node();this.controls[selector]=node;this.appendChild(node); }},get(){return this.markup;}});
  const document={hidden:false,activeElement:null,createElement:tag=>new Node(tag),createDocumentFragment:()=>new Node('fragment')};
  class Observer {
    constructor(callback,options) { this.callback=callback;this.options=options;this.disconnected=false;observers.push(this); }
    observe(target) { this.target=target; }
    disconnect() { this.disconnected=true; }
    enter() { this.callback([{target:this.target,isIntersecting:true}]); }
  }
  const extra={document,matchMedia:()=>({matches:wide}),store:{get:(key,fallback)=>fallback,set(){}},esc:String,ICON:{check:'',play:'play',link:'',dl:''}};
  if(automatic)extra.IntersectionObserver=Observer;
  const context=vm.createContext(extra);
  vm.runInContext(code+';fitNames=()=>{};globalThis.api={renderVersions,pauseVersionFeed,syncVersionFeeds};',context);
  const rows=Array.from({length:29},(_,i)=>source((i+1)+' GB','🌐 Browser 1080p','source-'+i));
  return {api:context.api,box,rows,observers,focused,document,Node,
    shown:()=>box.querySelector('.vgroup').querySelectorAll('.ver').length,
    status:()=>box.querySelector('.vsummary').innerHTML.replace(/<[^>]+>/g,'')};
}
test('scrolling appends bounded batches, updates totals and never takes focus', () => {
  const h=feedHarness();h.api.renderVersions(h.box,h.rows,null,()=>{},'movie/feed');
  assert.equal(h.shown(),12);assert.equal(h.status(),'12 of 29 options');
  assert.equal(h.observers[0].options.root,null);
  h.observers[0].enter();assert.equal(h.shown(),24);assert.equal(h.status(),'24 of 29 options');
  assert.equal(h.observers[0].disconnected,true);
  h.observers[0].enter();assert.equal(h.shown(),24,'a queued callback from an old observer must be ignored');
  h.observers.at(-1).enter();assert.equal(h.shown(),29);assert.equal(h.status(),'29 of 29 options');
  assert.equal(h.box.spy,null,'finished feeds release their observer');
  assert.equal(h.focused.length,0);
});
test('sheet feeds use their scroll root and pause cleanly while hidden or replaced', () => {
  const h=feedHarness({wide:false}),scroll=new h.Node();
  h.api.renderVersions(h.box,h.rows,scroll,()=>{},'series/feed');
  assert.equal(h.shown(),8);assert.equal(h.observers[0].options.root,scroll);
  h.box.visible=false;h.api.syncVersionFeeds();assert.equal(h.box.spy,null);
  h.observers[0].enter();assert.equal(h.shown(),8);
  h.box.visible=true;h.api.syncVersionFeeds();assert.ok(h.box.spy);h.observers.at(-1).enter();assert.equal(h.shown(),16);
  const old=h.observers.at(-1);h.api.renderVersions(h.box,h.rows.slice(0,3),scroll,()=>{},'series/other');
  assert.equal(old.disconnected,true);old.enter();assert.equal(h.shown(),3);assert.equal(h.status(),'3 of 3 options');
});
test('sorting starts a fresh feed and keyboard fallback loads more when observers are unavailable', () => {
  const h=feedHarness();h.api.renderVersions(h.box,h.rows,null,()=>{},'movie/sort');h.observers[0].enter();
  const old=h.observers.at(-1),select=h.box.querySelector('select');select.value='smallest';select.onchange();
  assert.equal(h.shown(),12);assert.equal(h.box.querySelector('.vgroup').children[0]._source.url,'source-0');
  old.enter();assert.equal(h.shown(),12);
  const fallback=feedHarness({automatic:false,wide:false});fallback.api.renderVersions(fallback.box,fallback.rows,null,()=>{},'movie/fallback');
  const button=fallback.box.querySelector('.vgroup').children.at(-1);assert.equal(button.tag,'button');
  fallback.document.activeElement=button;button.onclick();assert.equal(fallback.shown(),16);assert.equal(fallback.focused.length,1);
  assert.equal(fallback.status(),'16 of 29 options');
});
