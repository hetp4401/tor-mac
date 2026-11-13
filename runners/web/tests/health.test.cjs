const test=require('node:test');
const assert=require('node:assert/strict');
const h=require('../health.js');

test('missing telemetry is distinct from a measured zero',()=>{
  assert.equal(h.pct(null),'—');assert.equal(h.pct(0),'0%');
  assert.equal(h.rate(undefined),'—');assert.equal(h.rate(0),'0 B/s');
  assert.equal(h.bytes(NaN),'—');assert.equal(h.value(false),'—');
});
test('machine names and errors cannot inject dashboard markup',()=>{
  const row=h.machineRow({id:'1',name:'<img src=x onerror=alert(1)>',region:'<script>',state:'healthy'},1000);
  assert.ok(row.includes('&lt;img'));assert.ok(!row.includes('<img'));
  const card=h.macCard({id:'1',name:'Mac',state:'offline',error:'<script>x</script>'},1000);
  assert.ok(card.includes('&lt;script&gt;'));assert.ok(!card.includes('<script>'));
});
test('offline Mac retains explicitly marked last known work',()=>{
  const html=h.macCard({id:'3',name:'Mac mini 3',state:'offline',stale:true,jobs:2,capacity:3,last_seen:900},1000);
  assert.match(html,/Offline/);assert.match(html,/last known/);assert.doesNotMatch(html,/<i class="used"/);
});
test('trend gaps remain gaps rather than invented zero readings',()=>{
  const svg=h.chart([[1,10],[2,null],[3,20]],1,'CPU');
  assert.match(svg,/M4\.0,82\.0 M596\.0,74\.0/);
  assert.match(h.chart([[1,null]],1,'CPU'),/Waiting for trend history/);
});
test('Frame host memory trends weight machines by actual capacity',()=>{
  const rows=h.fleetTrends([{history:[[1,20,50,0,0,50,100]]},{history:[[1,40,10,0,0,90,900]]}]);
  assert.equal(rows[0][0],1);assert.equal(rows[0][1],30);assert.ok(Math.abs(rows[0][2]-14)<1e-9);
});
test('attention and search filters preserve stable machine ordering',()=>{
  const rows=[{id:'2',name:'Machine 2',state:'healthy',region:'west',runs_frame:true},{id:'1',name:'Machine 1',state:'offline',region:'east'},{id:'3',name:'Machine 3',state:'stale',region:'east'}];
  assert.deepEqual(h.filterMachines(rows,'east','attention').map(n=>n.id),['1','3']);
  assert.deepEqual(h.filterMachines(rows,'','all').map(n=>n.id),['1','3','2']);
});
test('Mac resource cards distinguish pending telemetry, stale readings, and measured zero',()=>{
  const mac={id:'1',name:'Mac mini 1',state:'healthy',online:true,resource_metrics:false};
  assert.match(h.macCard(mac,1000),/Host telemetry update pending/);
  assert.doesNotMatch(h.macCard(mac,1000),/0%/);
  Object.assign(mac,{resource_metrics:true,resource_sample_at:999,cpu_percent:0,memory_percent:25,disk_percent:30,network_rx:0,network_tx:1024});
  const html=h.macCard(mac,1000);
  assert.match(html,/0%/);assert.match(html,/25%/);assert.match(html,/30%/);
  assert.match(html,/↓ 0 B\/s/);assert.match(html,/↑ 1 KB\/s/);
  mac.resource_stale=true;
  assert.match(h.macCard(mac,1000),/Last known resources/);
});
test('replica card presents workload cores and memory separately from host usage',()=>{
  const html=h.replicaCard({name:'Machine <1>',sample_at:1000},{replica:2,state:'healthy',cpu_cores:1.25,memory_bytes:1024,network_rx:0},1000);
  assert.match(html,/Replica 2/);assert.match(html,/1.25 cores/);assert.match(html,/1 KB/);assert.match(html,/0 B\/s/);
  assert.match(html,/Machine &lt;1&gt;/);
});
test('GPU counters remain distinct from CPU, missing counters, and stale samples',()=>{
  const m={id:'1',name:'Mac mini 1',state:'healthy',online:true,resource_metrics:true,gpu_state:'available',gpu_sample_at:999,cpu_percent:70,gpu_percent:0,gpu_memory_used:1024,gpu_memory_allocated:4096};
  const html=h.macCard(m,1000);
  assert.match(html,/GPU memory in use/);assert.match(html,/Allocated 4 KB/);assert.match(html,/70%/);assert.match(html,/0%/);
  assert.match(h.gpuNote({...m,gpu_state:'unavailable'},1000),/not exposed/);
  assert.match(h.gpuNote({...m,gpu_stale:true},1000),/Last known GPU/);
  assert.match(h.gpuNote({...m,gpu_state:'error'},1000),/sample failed/);
  assert.match(h.gpuNote({...m,gpu_state:'pending'},1000),/update pending/);
  assert.match(h.gpuNote({...m,gpu_percent:null},1000),/utilization is not reported/);
  const partial=h.metricCharts({...m,gpu_percent:null,history:[]},'mac','gpu',900,1000);
  assert.match(partial,/GPU utilization is not reported/);assert.doesNotMatch(partial,/0%/);
});
test('multi-series charts preserve missing samples and time gaps',()=>{
  const svg=h.seriesChart([[0,10],[15,null],[30,20],[120,30]],[{name:'Core 1',get:r=>r[1]}],'CPU',{max:100,gap:45});
  assert.match(svg,/Core 1/);assert.match(svg,/30%/);
  assert.match(svg,/M6\.0,82\.8 M153\.0,74\.6 M594\.0,66\.4/);
  assert.match(h.seriesChart([[1,null]],[{name:'GPU',get:r=>r[1]}],'GPU'),/Collecting gpu history/);
});
test('CPU charts expose separate cores and range selection excludes older samples',()=>{
  const r=Array(20).fill(null);r[0]=999;r[4]=25;r[19]=[0,50];
  const m={cpu_per_core:[0,50],history:[[1,0,0,0,99],r]};
  const html=h.metricCharts(m,'mac','cpu',300,1000);
  assert.match(html,/Core 1/);assert.match(html,/Core 2/);assert.match(html,/0%/);assert.match(html,/50%/);
  assert.match(html,/1 samples/);assert.doesNotMatch(html,/99%/);
});
test('RAM disk network and GPU charts use their own recorded fields',()=>{
  const r=Array(20).fill(0);r[0]=999;r[9]=1024;r[10]=4096;r[13]=512;r[14]=2048;r[6]=4096;r[7]=8192;
  const m={gpu_state:'available',history:[r]};
  assert.match(h.metricCharts(m,'mac','memory',900,1000),/RAM in use/);
  assert.match(h.metricCharts(m,'mac','disk',900,1000),/2 KB\/s/);
  assert.match(h.metricCharts(m,'mac','network',900,1000),/8 KB\/s/);
  const gpu=h.metricCharts(m,'mac','gpu',900,1000);
  assert.match(gpu,/Renderer/);assert.match(gpu,/Tiler/);assert.match(gpu,/Per-GPU-core counters are not exposed/);
});
test('stream timing is labeled as segment preparation with sample count',()=>{
  const html=h.streamCards({segment_p50_ms:50,segment_p95_ms:100,segment_samples:2,modes:{'as-is':1,original:1,remux:2,converted:3}});
  assert.match(html,/Segment preparation/);assert.match(html,/Median · p95 100 ms · 2 samples/);assert.match(html,/2 \/ 2 \/ 3/);
  assert.doesNotMatch(html,/first frame|seek latency/i);
});
test('failed refresh and paused old snapshots cannot retain healthy badges',()=>{
  const data={macs:[{state:'healthy',online:true,jobs:2}],machines:[{state:'offline',online:false,cpu_percent:80}]};
  for(const [time,error] of [[46000,null],[1000,'HTTP 503']]) {
    const shown=h.displaySnapshot(data,0,error,time);
    assert.equal(shown.macs[0].state,'stale');assert.equal(shown.macs[0].online,false);
    assert.equal(shown.macs[0].jobs,2);assert.equal(shown.machines[0].state,'offline');
  }
  assert.equal(data.macs[0].state,'healthy');
  assert.equal(h.displaySnapshot(data,0,null,1000),data);
});
