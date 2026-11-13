(() => {
  'use strict';
  const number = n => typeof n === 'number' && Number.isFinite(n);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const value = (n, digits=0) => number(n) ? n.toLocaleString(undefined, {maximumFractionDigits:digits}) : '—';
  const pct = n => number(n) ? value(n, 1) + '%' : '—';
  const bytes = n => !number(n) ? '—' : n >= 2**30 ? value(n/2**30, 1)+' GB' : n >= 2**20 ? value(n/2**20, 1)+' MB' : n >= 1024 ? value(n/1024, 1)+' KB' : value(n)+' B';
  const rate = n => number(n) ? bytes(n)+'/s' : '—';
  const ms = n => number(n) ? n>=1000 ? value(n/1000,2)+' sec' : value(n)+' ms' : '—';
  const cores = n => number(n) ? value(n,2)+' cores' : '—';
  const age = (stamp, now=Date.now()/1000) => !number(stamp) ? 'Not reported' : now-stamp < 3 ? 'Just now' : now-stamp < 60 ? value(Math.max(0,now-stamp))+' sec ago' : now-stamp < 3600 ? value((now-stamp)/60)+' min ago' : value((now-stamp)/3600,1)+' hr ago';
  const labels = {healthy:'Healthy',busy:'Converting',offline:'Offline',stale:'Stale data',attention:'Needs attention',draining:'Draining',starting:'Starting',loading:'Checking'};
  const badge = state => `<span class="badge ${Object.hasOwn(labels,state) ? state : 'loading'}">${esc(labels[state] || 'Unknown')}</span>`;
  const sum = list => list.filter(number).reduce((a,b)=>a+b,0);
  const available = list => list.some(number) ? sum(list) : null;
  const average = list => { const ns=list.filter(number); return ns.length ? sum(ns)/ns.length : null; };
  const attention = n => ['offline','stale','attention'].includes(n.state);
  const meter = (n, blue=false) => `<span>${pct(n)}</span>${number(n) ? `<div class="meter${blue ? ' blue':''}${n>=90?' warn':''}"><i style="width:${Math.max(0,Math.min(100,n))}%"></i></div>`:''}`;
  function chart(rows, index, label, color='var(--accent)', max=100) {
    if (!rows.some(row=>number(row[index]))) return '<div class="chart-empty">Waiting for trend history</div>';
    const points = rows.filter(row=>number(row[0])), first=points[0]?.[0], span=Math.max(1,(points.at(-1)?.[0] || first)-first);
    let path='', connected=false;
    for (const row of points) {
      if (!number(row[index])) { connected=false; continue; }
      const x=4+(row[0]-first)/span*592, y=90-Math.min(max,Math.max(0,row[index]))/Math.max(1,max)*80;
      path+=(connected?'L':'M')+x.toFixed(1)+','+y.toFixed(1)+' '; connected=true;
    }
    return `<svg class="chart" viewBox="0 0 600 100" preserveAspectRatio="none" role="img" aria-label="${esc(label)}"><path d="M0 10H600 M0 50H600 M0 90H600" fill="none" stroke="var(--line)" stroke-dasharray="3 5"/><path d="${path}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  }
  function fleetTrends(nodes) {
    const samples=new Map();
    nodes.forEach(node=>(node.history || []).forEach(row=>{ if(!samples.has(row[0])) samples.set(row[0],[]); samples.get(row[0]).push(row); }));
    return [...samples].sort((a,b)=>a[0]-b[0]).map(([t,rows])=>{const paired=rows.filter(r=>number(r[5])&&number(r[6])&&r[6]>0), total=sum(paired.map(r=>r[6]));return [t,average(rows.map(r=>r[1])),total?sum(paired.map(r=>r[5]))/total*100:null];});
  }
  const colors=['#a99aff','#7aabff','#74dcb2','#f1c37b','#ff929f','#71d8e4','#e997ed','#badb7e'];
  const metrics={cpu:'CPU',gpu:'GPU',memory:'RAM',disk:'Disk',network:'Network',work:'Conversion'};
  function seriesChart(rows, specs, label, {max=null,format=pct,gap=45}={}) {
    rows=rows.filter(r=>number(r[0])).sort((a,b)=>a[0]-b[0]);
    const all=rows.flatMap(r=>specs.map(s=>s.get(r))).filter(number);
    if(!all.length) return '<div class="chart-empty">Collecting '+esc(label.toLowerCase())+' history…</div>';
    const ceiling=Math.max(1,max??Math.max(...all)*1.1), first=rows[0][0], last=rows.at(-1)[0], span=Math.max(1,last-first);
    const lines=specs.map((s,i)=>{
      let path='',previous=null,point=null;
      rows.forEach(r=>{const v=s.get(r);if(!number(v)){previous=null;return;}const x=6+(r[0]-first)/span*588,y=91-Math.min(ceiling,Math.max(0,v))/ceiling*82;
        path+=(previous!==null && r[0]-previous<=gap?'L':'M')+x.toFixed(1)+','+y.toFixed(1)+' ';previous=r[0];point=[x,y];});
      const color=s.color||colors[i%colors.length];
      return `<path d="${path}" fill="none" stroke="${color}" stroke-width="1.8" vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round"/>${point?`<circle cx="${point[0].toFixed(1)}" cy="${point[1].toFixed(1)}" r="2.2" fill="${color}"/>`:''}`;
    }).join('');
    const stamp=t=>new Date(t*1000).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'});
    return `<div class="series-scale"><span>${esc(format(ceiling))}</span><span>${rows.length} samples</span></div><svg class="series-chart" viewBox="0 0 600 100" preserveAspectRatio="none" role="img" aria-label="${esc(label)}"><path d="M0 9H600 M0 50H600 M0 91H600" fill="none" stroke="var(--line)" stroke-dasharray="3 5"/>${lines}</svg><div class="chart-caption"><span>${esc(stamp(first))}</span><span>${esc(stamp(last))}</span></div><div class="series-legend">${specs.map((s,i)=>`<span><i style="background:${s.color||colors[i%colors.length]}"></i>${esc(s.name)}<b>${esc(format(s.get(rows.at(-1))))}</b></span>`).join('')}</div>`;
  }
  function metricCharts(m, kind, metric, range, now) {
    const mac=kind==='mac', rows=(m.history||[]).filter(r=>r[0]>=now-range), observed=(m.observed_history||[]).filter(r=>r[0]>=now-range);
    const series=(name,index,color)=>({name,get:r=>r[index],color}), options={gap:mac?45:120};
    const block=(title,data,specs,extra={})=>`<div class="metric-chart"><h3>${esc(title)}</h3>${seriesChart(data,specs,title,{...options,...extra})}</div>`;
    if(metric==='cpu') {
      const specs=[series('Total',mac?4:1)];
      if(mac) for(let i=0;i<Math.max(m.cpu_per_core?.length||0,...rows.map(r=>r[19]?.length||0));i++)specs.push({name:'Core '+(i+1),get:r=>r[19]?.[i]});
      return block('CPU utilization',rows,specs,{max:100})+(!mac?'<p class="footnote">This host’s metrics feed reports total CPU; per-core readings are unavailable.</p>':'');
    }
    if(metric==='gpu') return !mac?'<p class="footnote">GPU metrics are collected on the Mac workers.</p>':
      (m.gpu_state!=='available'?`<p class="footnote">${esc(gpuNote(m,now))}</p>`:'')+
      (m.gpu_state==='available'&&!number(m.gpu_percent)&&!rows.some(r=>number(r[8]))?'<div class="metric-chart"><h3>GPU activity</h3><div class="chart-empty">GPU utilization is not reported by this host.</div></div>':block('GPU activity',rows,[series('GPU',8),series('Renderer',17),series('Tiler',18)],{max:100}))+block('GPU memory',rows,[series('In use',15),series('Allocated',16)],{format:bytes})+'<p class="footnote">Per-GPU-core counters are not exposed. Renderer and tiler are activity counters, not individual cores.</p>';
    if(metric==='memory') return block('RAM in use',rows,[series('Used',mac?9:5),series('Total',mac?10:6)],{format:bytes})+block('RAM utilization',rows,[series('Used',mac?5:2)],{max:100});
    if(metric==='network') return block('Network traffic',rows,[series('Received ↓',mac?6:3),series('Sent ↑',mac?7:4)],{format:rate});
    if(metric==='disk') return block('Disk I/O',rows,[series('Read',mac?13:7),series('Write',mac?14:8)],{format:rate})+block('Disk utilization',mac?rows:observed,[series('Used',mac?11:9)],{max:100})+block('Available disk space',mac?rows:observed,[series('Free',mac?12:10)],{format:bytes});
    return mac?block('Active conversion jobs',rows,[series('Jobs',1)],{max:Math.max(1,m.capacity||1),format:value})+block('Recent conversion median',rows,[series('Conversion',3)],{format:n=>number(n)?value(n,2)+' sec':'—'})+block('Health response',rows,[series('Response',2)],{format:ms}):'<p class="footnote">Conversion metrics are collected on the Mac workers.</p>';
  }
  function chartControls(metric, range, global=false) {
    return `<div class="chart-controls"><div class="metric-tabs" role="group" aria-label="Metric">${Object.entries(metrics).map(([key,label])=>`<button type="button" data-${global?'global-':''}metric="${key}" aria-pressed="${key===metric}">${label}</button>`).join('')}</div><div class="range-tabs" role="group" aria-label="Chart range">${[[300,'5 min'],[900,'15 min'],[3600,'1 hour']].map(([n,label])=>`<button type="button" data-${global?'global-':''}range="${n}" aria-pressed="${n===range}">${label}</button>`).join('')}</div></div>`;
  }
  function macCard(m, now, metric='cpu', range=900) {
    const slots=number(m.capacity)?Math.min(16,m.capacity):0;
    return `<article class="mac-card ${m.state==='offline'?'offline':''}"><div class="mac-head"><button class="machine-button" data-open="mac:${esc(m.id)}">${esc(m.name)}</button>${badge(m.state)}</div>${resourceGrid(m)}<p class="telemetry-note${m.resource_stale?' error-text':''}">${esc(resourceNote(m,now))}</p><p class="telemetry-note${m.gpu_stale?' error-text':''}">${esc(gpuNote(m,now))}</p><div class="mac-chart">${metricCharts(m,'mac',metric,range,now)}</div><div class="jobs${m.stale?' stale-reading':''}"><strong>${value(m.jobs)}</strong><span>/${value(m.capacity)} conversion jobs${m.stale?' · last known':''}</span></div><div class="capacity" aria-hidden="true">${Array.from({length:slots},(_,i)=>`<i class="${!m.stale && i<m.jobs?'used':''}"></i>`).join('')}</div><div class="mac-metrics"><div><span>Conversion median</span><b>${number(m.conversion_s)?value(m.conversion_s,2)+' sec':'—'}</b></div><div><span>Health response</span><b>${ms(m.latency_ms)}</b></div><div><span>Completed</span><b>${value(m.done)}</b></div><div><span>Recorded stuck</span><b>${value(m.stuck)}</b></div></div><div class="mac-foot"><span><b>${value(m.failed)}</b> failed</span><span><b>${value(m.canceled)}</b> canceled</span><span>Since worker start</span></div><p class="last-seen">${m.error?esc(m.error)+' · ':''}Seen ${esc(age(m.last_seen,now).toLowerCase())}</p></article>`;
  }
  function resourceNote(m, now) {
    return !m.resource_metrics ? m.online ? 'Host telemetry update pending' : 'Host telemetry unavailable' : m.resource_stale || m.stale ? 'Last known resources · '+age(m.resource_sample_at,now).toLowerCase() : 'Resources sampled '+age(m.resource_sample_at,now).toLowerCase();
  }
  function gpuNote(m, now) {
    if(m.gpu_state==='unavailable') return m.gpu_reason || 'GPU counters are not exposed by this Mac';
    if(m.gpu_state==='error') return 'GPU sample failed · '+(number(m.gpu_sample_at)&&m.gpu_sample_at>0?'last known '+age(m.gpu_sample_at,now).toLowerCase():'readings unavailable');
    if(m.gpu_state!=='available') return m.online?'GPU telemetry update pending':'GPU telemetry unavailable';
    if(m.gpu_stale || m.stale) return 'Last known GPU readings · '+age(m.gpu_sample_at,now).toLowerCase();
    if(!number(m.gpu_percent)) return 'GPU memory available · utilization is not reported by this host';
    return m.gpu_devices>1?'GPU shows the busiest adapter':'GPU memory shares system RAM on Apple silicon';
  }
  function resourceGrid(m) {
    return `<div class="resource-grid${m.resource_stale || m.stale?' stale-reading':''}"><div><span>CPU</span><div class="resource-value">${meter(m.cpu_percent)}</div><small>${value(m.cpus)} cores</small></div><div><span>RAM</span><div class="resource-value">${meter(m.memory_percent,true)}</div><small>${bytes(m.memory_used)} / ${bytes(m.memory_total)}</small></div><div class="${m.gpu_stale?'stale-reading':''}"><span>GPU</span><div class="resource-value">${meter(m.gpu_percent)}</div><small>Graphics activity</small></div><div class="${m.gpu_stale?'stale-reading':''}"><span>GPU memory in use</span><div class="resource-value">${bytes(m.gpu_memory_used)}</div><small>Allocated ${bytes(m.gpu_memory_allocated)}</small></div><div><span>Disk used</span><div class="resource-value">${meter(m.disk_percent)}</div><small>${bytes(m.disk_free)} free</small></div><div><span>Network</span><b class="network-value">↓ ${rate(m.network_rx)}<br>↑ ${rate(m.network_tx)}</b><small>Received / sent</small></div></div>`;
  }
  function replicaCard(m, w, now) {
    return `<article class="replica-card${m.stale?' stale-reading':''}"><div class="mac-head"><h3>Replica ${value(w.replica)}</h3>${badge(w.state || (m.stale?'stale':'starting'))}</div><p class="detail-meta">${esc(m.name)} · ${esc(age(m.sample_at,now).toLowerCase())}${m.stale?' · last known':''}</p><dl class="workload-grid"><div><dt>CPU</dt><dd>${cores(w.cpu_cores)}</dd></div><div><dt>Memory</dt><dd>${bytes(w.memory_bytes)}</dd></div><div><dt>Network ↓ / ↑</dt><dd>${rate(w.network_rx)} / ${rate(w.network_tx)}</dd></div><div><dt>Disk read / write</dt><dd>${rate(w.disk_read)} / ${rate(w.disk_write)}</dd></div></dl></article>`;
  }
  function streamCards(s) {
    const modes=s.modes || {}, original=sum([modes['as-is'],modes.original]), remux=modes.remux, converted=modes.converted;
    const fields=[['Segment preparation',ms(s.segment_p50_ms),`Median · p95 ${ms(s.segment_p95_ms)} · ${value(s.segment_samples)} samples`],['Source fetch speed',number(s.source_fetch_mbps)?value(s.source_fetch_mbps,1)+' Mbps':'—',`${value(s.paused_sources)} sources backing off`],['Playback modes',`${value(original)} / ${value(remux || 0)} / ${value(converted || 0)}`,'Original / remux / converted'],['Media cache',bytes(s.cache_bytes),`${value(s.cpu_jobs)} CPU jobs · ${value(s.mac_jobs)} Mac requests`]];
    return fields.map(([label,reading,detail])=>`<div class="stream-tile"><span>${esc(label)}</span><strong>${esc(reading)}</strong><small>${esc(detail)}</small></div>`).join('');
  }
  function machineRow(m, now) {
    return `<tr class="${m.stale?'stale-reading':''}"><td><button class="machine-button" data-open="host:${esc(m.id)}">${esc(m.name)}</button><span class="sub replica-label">${esc((m.replicas || []).map(n=>'Replica '+value(n)).join(' · '))}</span><span class="sub">${esc(m.region || 'Region unknown')} · ${esc(m.pool || 'Unassigned')}</span></td><td>${badge(m.state)}</td><td data-label="CPU">${meter(m.cpu_percent)}</td><td data-label="Memory">${meter(m.memory_percent,true)}</td><td data-label="Disk used">${meter(m.disk_percent)}</td><td data-label="Network ↓ / ↑">${rate(m.network_rx)} / ${rate(m.network_tx)}</td><td data-label="Sample">${esc(age(m.sample_at,now))}${m.stale?'<span class="sub">Last known values</span>':''}</td></tr>`;
  }
  function filterMachines(nodes, search, filter) {
    const query=search.trim().toLowerCase();
    return nodes.filter(m=>(filter==='all' || filter==='attention' && attention(m)) && `${m.name} ${m.id} ${m.region || ''} ${m.pool || ''}`.toLowerCase().includes(query)).sort((a,b)=>Number(attention(b))-Number(attention(a)) || Number(a.id)-Number(b.id));
  }
  function displaySnapshot(data, received, error, now=Date.now()) {
    if(!data || !error && now-received<=45000) return data;
    const old=n=>({...n,online:false,stale:true,resource_stale:true,gpu_stale:true,workloads:n.workloads?.map(w=>({...w,state:'stale'})),state:['offline','loading'].includes(n.state)?n.state:'stale'});
    return {...data,macs:data.macs.map(old),machines:data.machines.map(old)};
  }
  const exported={number,esc,value,pct,bytes,rate,age,badge,chart,fleetTrends,macCard,machineRow,filterMachines,displaySnapshot,replicaCard,streamCards,resourceNote,gpuNote,seriesChart,metricCharts,chartControls};
  if(typeof module!=='undefined' && module.exports) module.exports=exported;
  if(typeof document==='undefined') return;
  const $=id=>document.getElementById(id), S={data:null,error:null,paused:false,timer:null,request:null,received:0,detail:null,metric:'cpu',range:900,macMetric:'cpu',macRange:900};
  function viewData() { return displaySnapshot(S.data,S.received,S.error); }
  function safeRunners(base) { try { const u=new URL(base); return u.protocol==='https:' && !u.username && !u.password ? u.origin : 'https://runners.billybishop4-workers.xyz'; } catch { return 'https://runners.billybishop4-workers.xyz'; } }
  function freshness() {
    const old=Date.now()-S.received>45000, failed=S.error || old;
    $('freshness').textContent=S.paused?'Updates paused':document.hidden?'Updates paused while hidden':S.error?'Refresh failed · showing last known data':!S.data?'Connecting to your machines…':S.data.refreshing && !Object.keys(S.data.sources).length?'Checking services…':`${old?'Last update':'Updated'} ${age(S.received/1000).toLowerCase()}`;
    $('freshness').className=failed?'stale':S.data&&!S.paused?'live':'';
    $('refresh').disabled=!!S.request;
  }
  function renderTable() {
    if(!S.data) return;
    const rows=filterMachines(viewData().machines,$('search').value,$('filter').value);
    $('machines').innerHTML=rows.map(m=>machineRow(m,Date.now()/1000)).join('') || '<tr><td colspan="7" class="empty">No machines match this view.</td></tr>';
    $('table-count').textContent=`${rows.length} of ${S.data.machines.length}`;
  }
  function render() {
    if(!S.data) { freshness(); return; }
    const d=viewData(), now=Date.now()/1000, nodes=[...d.macs,...d.machines], online=nodes.filter(n=>n.online).length;
    const focus=document.activeElement?.dataset?.open;
    const known=d.macs.filter(m=>!m.stale), jobs=available(known.map(m=>m.jobs));
    const tiles=[['Machines online',`${online}<em>/ ${nodes.length || '—'}</em>`,`${nodes.filter(attention).length} need attention`],['Active streams',value(d.service.sessions),'Playback service · replica '+d.service.replica],['Mac conversion jobs',value(jobs),`${known.length} of ${d.macs.length} workers reporting`],['Queued work',value(d.service.queued),`${value(d.service.reads)} reads · ${value(d.service.media_processes)} media processes`]];
    $('summary').innerHTML=tiles.map(([label,note,sub])=>`<div class="summary-tile"><span>${esc(label)}</span><strong>${note}</strong><small>${esc(sub)}</small></div>`).join('');
    $('mac-count').textContent=d.macs.length; $('fleet-count').textContent=d.machines.length;
    $('mac-chart-controls').innerHTML=chartControls(S.macMetric,S.macRange,true);
    $('macs').innerHTML=d.macs.map(m=>macCard(m,now,S.macMetric,S.macRange)).join('') || '<div class="empty">No Mac workers are configured.</div>';
    const workloads=d.machines.flatMap(m=>(m.workloads || []).map(w=>[m,w]));
    $('replica-count').textContent=workloads.length;
    $('replicas').innerHTML=workloads.map(([m,w])=>replicaCard(m,w,now)).join('') || '<div class="empty">Waiting for Frame replica placements.</div>';
    $('streaming').innerHTML=streamCards(d.service);
    $('stream-scope').textContent=`Frame replica ${d.service.replica} · server timings for recent segments`;
    const notices=[];
    if(S.error) notices.push('The latest dashboard update failed. Values below are from the last successful update.');
    for(const name of ['fleet','metrics']) if(d.sources[name]?.error) notices.push(`${name==='fleet'?'Frame host availability':'Host resource metrics'} could not refresh (${d.sources[name].error}).`);
    const badMac=d.macs.filter(m=>attention(m));
    if(badMac.length) notices.push(badMac.map(m=>m.name+': '+(labels[m.state]||m.state).toLowerCase()).join(' · ')+'.');
    $('notice').textContent=notices.join(' '); $('notice').hidden=!notices.length;
    const fresh=d.machines.filter(m=>!m.stale), trend=fleetTrends(d.machines);
    $('cpu-value').textContent=pct(average(fresh.map(m=>m.cpu_percent)));
    const paired=fresh.filter(m=>number(m.memory_total)&&number(m.memory_used)&&m.memory_total>0), total=available(paired.map(m=>m.memory_total)), used=available(paired.map(m=>m.memory_used));
    $('memory-value').textContent=number(used)&&total?pct(used/total*100):'—';
    $('cpu-chart').innerHTML=chart(trend,1,'Frame host average CPU over the last hour');
    $('memory-chart').innerHTML=chart(trend,2,'Frame host memory percentage over the last hour','var(--blue)');
    $('runners-link').href=safeRunners(d.runners_url)+'/#/apps/frame';
    $('service-detail').textContent=`Frame replica ${d.service.replica} · ${d.service.code} · ${bytes(d.service.cache_bytes)} media cache`;
    renderTable(); freshness(); if(S.detail) renderDetail();
    if(focus) [...document.querySelectorAll('[data-open]')].find(el=>el.dataset.open===focus)?.focus({preventScroll:true});
  }
  function renderDetail() {
    const [kind,id]=S.detail.split(':'), d=viewData(), m=(kind==='mac'?d.macs:d.machines).find(n=>n.id===id);
    if(!m) { $('detail-body').textContent='This machine is no longer listed.'; return; }
    $('detail-kind').textContent=kind==='mac'?'MAC CONVERSION WORKER':'FRAME REPLICA HOST'; $('detail-title').textContent=m.name;
    const fields=[['Host CPU',pct(m.cpu_percent)+' · '+value(m.cpus)+' cores'],['Memory',bytes(m.memory_used)+' / '+bytes(m.memory_total)],['Free disk',bytes(m.disk_free)+' / '+bytes(m.disk_total)],['Network received',rate(m.network_rx)],['Network sent',rate(m.network_tx)],['Disk read / write',rate(m.disk_read)+' / '+rate(m.disk_write)],['Resource sample',age(kind==='mac'?m.resource_sample_at:m.sample_at)],...(kind==='mac'?[['GPU utilization',pct(m.gpu_percent)],['GPU renderer / tiler',pct(m.gpu_renderer_percent)+' / '+pct(m.gpu_tiler_percent)],['GPU memory in use',bytes(m.gpu_memory_used)],['GPU memory allocated',bytes(m.gpu_memory_allocated)],['GPU driver',m.gpu_name||'Not reported'],['GPU sample',age(m.gpu_sample_at)],['Jobs',value(m.jobs)+' / '+value(m.capacity)],['Completed',value(m.done)],['Failed',value(m.failed)],['Canceled',value(m.canceled)],['Recorded stuck',value(m.stuck)],['Engine',m.engine||'Not reported'],['Median of last 8 conversions',number(m.conversion_s)?value(m.conversion_s,3)+' sec':'—'],['Last conversion',age(m.last_conversion_at)],['Health response',ms(m.latency_ms)],['Network errors ↓ / ↑',value(m.network_errors_rx,2)+' / '+value(m.network_errors_tx,2)+' per sec'],['Dropped packets ↓ / ↑',value(m.network_drops_rx,2)+' / '+value(m.network_drops_tx,2)+' per sec'],['Host uptime',number(m.uptime_s)?value(m.uptime_s/3600,1)+' hours':'—'],['Collector time',ms(m.sample_cost_ms)],['Revision',m.revision||'—']]:[['Host response',ms(m.latency_ms)],['Frame replicas',(m.replicas || []).map(n=>value(n)).join(', ')],['Running since',number(m.started_at)?new Date(m.started_at*1000).toLocaleString():'—']])];
    const top=$('detail').scrollTop;
    $('detail-body').innerHTML=badge(m.state)+`<p class="detail-meta">${kind==='mac'?'Counts reset when the worker restarts.':esc(m.location||m.region||'Location not reported')} · Last seen ${esc(age(m.last_seen).toLowerCase())}</p>`+(m.stale?'<p class="error-text">These are last known readings. Current usage is unavailable.</p>':'')+(kind==='mac'?`<p class="detail-meta">${esc(gpuNote(m,Date.now()/1000))}. GPU readings describe graphics activity; conversion jobs and timings show encoding workload.</p>`:'')+(m.error?`<p class="error-text">${esc(m.error)}</p>`:'')+(m.reasons?.length?`<p class="error-text">${esc(m.reasons.join(' · '))}</p>`:'')+chartControls(S.metric,S.range)+`<div class="detail-charts">${metricCharts(m,kind,S.metric,S.range,Date.now()/1000)}</div><dl class="detail-grid">${fields.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>`+(kind==='mac'?'':`<a class="detail-link" href="${esc(safeRunners(S.data.runners_url))}/#/machines/${encodeURIComponent(m.id)}" target="_blank" rel="noopener noreferrer">View machine in Runners ↗</a>`);
    $('detail').scrollTop=top;
  }

  function schedule(seconds=15) { clearTimeout(S.timer); if(!S.paused&&!document.hidden) S.timer=setTimeout(refresh,seconds*1000); }
  async function refresh() {
    if(S.request) return;
    const request=new AbortController(); S.request=request; freshness();
    const timeout=setTimeout(()=>request.abort(),8000);
    try {
      const r=await fetch('/api/health',{cache:'no-store',signal:request.signal}); if(!r.ok) throw Error('HTTP '+r.status);
      const data=await r.json(); if(!Array.isArray(data.macs)||!Array.isArray(data.machines)||!data.service||!data.sources) throw Error('Invalid health response');
      S.data=data; S.received=Date.now(); S.error=null; S.ageStale=false;
    } catch(error) { if(!document.hidden) S.error=error.name==='AbortError'?'Request timed out':error.message; }
    finally { clearTimeout(timeout); S.request=null; render(); if(!S.data&&S.error){$('notice').hidden=false;$('notice').textContent='Health metrics could not load. Use Refresh to retry.';} schedule(S.error?30:S.data?.refreshing?3:15); }
  }
  $('refresh').addEventListener('click',()=>{clearTimeout(S.timer);refresh();});
  $('pause').addEventListener('click',()=>{S.paused=!S.paused;$('pause').textContent=S.paused?'Resume updates':'Pause updates';$('pause').setAttribute('aria-pressed',String(S.paused));clearTimeout(S.timer);freshness();if(!S.paused)refresh();});
  $('search').addEventListener('input',renderTable); $('filter').addEventListener('change',renderTable);
  document.addEventListener('click',e=>{const button=e.target.closest('[data-open]');if(button){S.detail=button.dataset.open;renderDetail();$('detail').showModal();}});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-metric],[data-range],[data-global-metric],[data-global-range]');if(!b)return;
    if(b.dataset.globalMetric){S.macMetric=b.dataset.globalMetric;render();}
    else if(b.dataset.globalRange){S.macRange=Number(b.dataset.globalRange);render();}
    else {if(b.dataset.metric)S.metric=b.dataset.metric;else S.range=Number(b.dataset.range);renderDetail();}
  });
  $('close-detail').addEventListener('click',()=>$('detail').close()); $('detail').addEventListener('close',()=>{S.detail=null;});
  document.addEventListener('visibilitychange',()=>{clearTimeout(S.timer);if(document.hidden)S.request?.abort();else if(!S.paused)refresh();freshness();});
  window.addEventListener('pagehide',()=>{clearTimeout(S.timer);S.request?.abort();});
  window.addEventListener('pageshow',e=>{if(e.persisted&&!S.paused)refresh();});
  setInterval(()=>{if(S.data&&Date.now()-S.received>45000&&!S.ageStale){S.ageStale=true;render();}else freshness();},5000); refresh();
})();
