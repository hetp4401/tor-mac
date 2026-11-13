const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../versions.js'), 'utf8').split('const sheet =')[0];
function api(extra = {}) {
  const context = vm.createContext({console, ...extra});
  vm.runInContext(code + '\nglobalThis.api = {versionBytes, sortedVersions, applePlayer, verTop, versionPlayLabel, verRow, trapDialogTab};', context);
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
test('external-player files copy their link while browser-ready rows start playback synchronously', () => {
  const copied=[], played=[];
  const {verRow,versionPlayLabel}=api({URL,location:{href:'https://fleet.test/watch'},esc:s=>String(s),ICON:{check:'check',play:'play',link:'link',dl:'download'},
    navigator:{clipboard:{writeText:async url=>copied.push(url)}},toast:()=>{},
    document:{createElement:()=>({dataset:{},controls:{'.vplay':{},'.cp':{}},querySelector(q){return this.controls[q];}})}});
  const direct={...source('12 GB','📄 Direct','/file/a/movie.mkv'),download:'/dl/a/movie.mkv'};
  const row=verRow(direct,s=>played.push(s));
  assert.match(versionPlayLabel(direct),/^Copy link for an external player:/);
  assert.match(row.innerHTML,/External player/);
  row.querySelector('.vplay').onclick();
  assert.deepEqual(copied,['https://fleet.test/file/a/movie.mkv']);
  assert.equal(played.length,0,'raw file must not be loaded into browser player');
  const original=source('12 GB');
  verRow(original,s=>played.push(s)).querySelector('.vplay').onclick();
  assert.equal(played[0],original,'browser playback retains the original user activation');
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
