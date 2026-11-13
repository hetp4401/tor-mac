import concurrent.futures
import json
import shutil
import threading
import time
import unittest
import urllib.error

from playback_harness import backend


class HealthDashboardTests(unittest.TestCase):
    def setUp(self):
        self.m = backend()
        self.m.APP = 'frame'
        self.now = 1000
        self.run = {'id':'new-run', 'machine':1, 'started':900000, 'seen':990000, 'live':True,
                    'ready':True, 'cpus':4, 'region':'us-west', 'projects':{'frame':{'s':'healthy'}},
                    'disk':{'total':1000, 'free':800}}
        self.sample = {'run':'new-run', 't':980000, 'h':{'cpu':12, 'memUsed':200, 'memTotal':1000, 'netRx':0}}
        self.state = {'data':{'runs':[self.run], 'slots':[1]}, 'at':995}
        self.metrics = {'data':{'live':{'1':self.sample}, 'm':{}, 't':[]}, 'at':995}

    def tearDown(self):
        shutil.rmtree(self.m.SEG_DIR, ignore_errors=True)

    def node(self):
        return self.m.health_fleet(self.state,self.metrics,self.now)[0]

    def test_machine_resources_and_real_zero_are_preserved(self):
        row=self.node()
        self.assertEqual(row['state'],'healthy')
        self.assertEqual(row['cpu_percent'],12)
        self.assertEqual(row['memory_percent'],20)
        self.assertEqual(row['disk_percent'],20)
        self.assertEqual(row['network_rx'],0)
        self.assertIsNone(row['network_tx'])
        self.assertTrue(row['runs_frame'])

    def test_replacement_deduplicates_slot_and_never_uses_previous_run_metrics(self):
        old={**self.run,'id':'old-run','started':800000}
        self.state['data']['runs'].append(old)
        self.sample['run']='old-run'
        rows=self.m.health_fleet(self.state,self.metrics,self.now)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['state'],'stale')
        self.assertIsNone(rows[0]['cpu_percent'])
        self.assertTrue(rows[0]['online'])

    def test_stale_samples_and_offline_heartbeats_do_not_look_healthy(self):
        self.sample['t']=700000
        self.assertEqual(self.node()['state'],'stale')
        self.run['seen']=700000
        self.assertEqual(self.node()['state'],'offline')
        self.assertFalse(self.node()['online'])

    def test_upstream_failure_keeps_last_values_and_marks_them_stale(self):
        self.metrics['error']='HTTP 503'
        row=self.node()
        self.assertEqual(row['cpu_percent'],12)
        self.assertEqual(row['state'],'stale')
        self.assertTrue(row['stale'])

    def test_missing_metrics_are_unknown_not_zero(self):
        self.metrics['data']['live']={}
        row=self.node()
        self.assertIsNone(row['cpu_percent'])
        self.assertIsNone(row['memory_percent'])
        self.assertIsNone(row['network_rx'])
        self.assertEqual(row['state'],'stale')

    def test_pressure_and_unhealthy_apps_are_visible(self):
        self.sample['h']['memUsed']=950
        self.run['projects']['frame']['s']='unhealthy'
        row=self.node()
        self.assertEqual(row['state'],'attention')
        self.assertIn('High memory use',row['reasons'])
        self.assertIn('1 unhealthy Frame replicas',row['reasons'])

    def test_only_current_frame_placements_contribute_hosts_and_metrics(self):
        self.state['data']['projects']=[{'name':'frame','placed':[{'machine':1,'replica':1}]}]
        unrelated={**self.run,'id':'other','machine':2,'projects':{'dosa':{'s':'healthy'}}}
        former={**self.run,'id':'former','machine':3}
        self.state['data']['runs'] += [unrelated,former]
        self.state['data']['slots']=[1,2,3,4]
        self.metrics['data']['live']['2']={'run':'other','t':990000,'h':{'cpu':99}}
        rows=self.m.health_fleet(self.state,self.metrics,self.now)
        self.assertEqual([r['id'] for r in rows],['1'])
        self.assertEqual(rows[0]['replicas'],[1])

    def test_missing_or_empty_frame_placement_never_falls_back_to_the_whole_fleet(self):
        for projects in ([],[{'name':'dosa','placed':[{'machine':1,'replica':1}]}],[{'name':'frame','placed':[]}]):
            self.state['data']['projects']=projects
            self.assertEqual(self.m.health_fleet(self.state,self.metrics,self.now),[])

    def test_placement_tracks_move_and_includes_missing_or_offline_replica_host(self):
        self.state['data']['projects']=[{'name':'frame','placed':[{'machine':2,'replica':1},{'machine':2,'replica':2}]}]
        rows=self.m.health_fleet(self.state,self.metrics,self.now)
        self.assertEqual([r['id'] for r in rows],['2'])
        self.assertEqual(rows[0]['replicas'],[1,2])
        self.assertEqual(rows[0]['state'],'offline')

    def test_similarly_named_apps_and_other_apps_failures_are_not_frame_health(self):
        self.run['projects']['frame-reports']={'s':'unhealthy'}
        self.run['projects']['dosa']={'s':'unhealthy'}
        self.assertEqual(self.node()['state'],'healthy')
        self.assertEqual(self.node()['apps'],1)
        self.run['projects'].pop('frame')
        self.assertEqual(self.m.health_fleet(self.state,self.metrics,self.now),[])

    def test_mac_failure_retains_last_success_without_counting_it_online(self):
        source={'data':{'ok':True,'jobs':2,'max':3,'done':40},'at':990,'checked':1000,'error':'HTTP 530'}
        row=self.m.health_mac('frame-mac-3',source,self.now)
        self.assertEqual(row['state'],'offline')
        self.assertFalse(row['online'])
        self.assertEqual(row['jobs'],2)
        self.assertTrue(row['stale'])
        self.assertIsNone(self.m.health_mac('frame-mac-1',{},self.now)['jobs'])

    def test_mac_median_cancellation_and_stuck_jobs_are_distinct(self):
        row=self.m.health_mac('frame-mac-1',{'data':{'jobs':0,'stuck':1,'failed':0,'ended':6,'recent':[{'convert_s':1},{'convert_s':3}]},'at':999},self.now)
        self.assertEqual(row['conversion_s'],2)
        self.assertEqual(row['state'],'attention')
        self.assertEqual(row['failed'],0)
        self.assertEqual(row['canceled'],6)

    def test_output_omits_raw_project_configuration_and_secrets(self):
        self.run.update(publicTunnels={'key':'do-not-forward'},secret='do-not-forward')
        self.run['projects']['frame']['config']='do-not-forward'
        self.state['data']['projects']=[{'name':'frame','env':{'PASSWORD':'do-not-forward'},'placed':[{'machine':1,'replica':1}]}]
        self.assertNotIn('do-not-forward',json.dumps(self.node()))

    def test_many_dashboard_visitors_share_one_bounded_refresh(self):
        m=self.m
        m.MAC_URLS=['https://frame-mac-'+str(i)+'.test' for i in (1,2,3)]
        gate=threading.Event(); calls=[]
        def read(url):
            calls.append(url); gate.wait(2)
            if '/api/status' in url:return {'runs':[],'slots':[]}
            if '/api/metrics' in url:return {'live':{},'m':{},'t':[]}
            return {'ok':True,'jobs':0,'max':3,'recent':[]}
        m.health_read=read
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda _:m.health_dashboard(),range(30)))
        gate.set()
        deadline=time.monotonic()+3
        while m.health_state['refreshing'] and time.monotonic()<deadline:time.sleep(.01)
        self.assertFalse(m.health_state['refreshing'])
        self.assertEqual(len(calls),5)
        row=m.health_dashboard()
        self.assertEqual(len(calls),5)
        self.assertTrue(all(n['online'] for n in row['macs']))
        self.assertTrue(all(n['latency_ms'] is not None for n in row['macs']))
        self.assertEqual(m.playback_sessions,{})
        self.assertEqual(m.cpu_jobs,{})
        self.assertEqual(m.mac_jobs,{})

    def test_refresh_failure_retains_last_sample_and_records_a_history_gap(self):
        m=self.m;m.MAC_URLS=['https://frame-mac-1.test']
        m.health_state['sources']['mac:frame-mac-1']={'data':{'ok':True,'jobs':2},'at':900}
        def read(url):
            raise urllib.error.HTTPError(url,530,'unavailable',{},None)
        m.health_read=read
        m.health_refresh()
        source=m.health_state['sources']['mac:frame-mac-1']
        self.assertEqual(source['at'],900)
        self.assertEqual(source['data']['jobs'],2)
        self.assertEqual(source['error'],'HTTP 530')
        self.assertEqual(list(m.health_state['history']['mac:frame-mac-1'])[-1][1:],[None]*len(m.HEALTH_MAC_HISTORY))

    def test_gpu_availability_and_staleness_do_not_masquerade_as_cpu_or_zero(self):
        source={'data':{'ok':True,'resources':{'sample_at':995,'cpu_percent':75,'gpu_state':'available',
                'gpu_sample_at':995,'gpu_percent':0,'gpu_memory_used':1024,'gpu_memory_allocated':4096}},'at':999}
        row=self.m.health_mac('frame-mac-1',source,self.now)
        self.assertEqual(row['gpu_percent'],0);self.assertEqual(row['cpu_percent'],75)
        self.assertFalse(row['gpu_stale']);self.assertEqual(row['gpu_memory_used'],1024)
        source['data']['resources'].update(gpu_state='error',gpu_error='Collector failed')
        row=self.m.health_mac('frame-mac-1',source,self.now)
        self.assertTrue(row['gpu_stale']);self.assertFalse(row['resource_stale'])
        source['data']['resources'].update(gpu_state='unavailable',gpu_error=None,gpu_percent=None)
        row=self.m.health_mac('frame-mac-1',source,self.now)
        self.assertIsNone(row['gpu_percent']);self.assertEqual(row['gpu_state'],'unavailable')

    def test_resource_history_records_core_memory_disk_and_gpu_series_with_stale_gaps(self):
        m=self.m;m.MAC_URLS=['https://frame-mac-1.test'];now=time.time()
        resources={'sample_at':now,'cpu_percent':25,'cpu_per_core':[0,50],'memory_used':100,'memory_total':200,
                   'disk_percent':20,'disk_free':800,'disk_read':10,'disk_write':20,'network_rx':30,'network_tx':40,
                   'gpu_state':'available','gpu_sample_at':now,'gpu_percent':0,'gpu_memory_used':50,'gpu_memory_allocated':80,
                   'gpu_renderer_percent':0,'gpu_tiler_percent':0}
        self.run['seen']=now*1000;self.sample['t']=now*1000
        def read(url):
            if '/api/status' in url:return self.state['data']
            if '/api/metrics' in url:return self.metrics['data']
            return {'ok':True,'jobs':0,'resources':resources}
        m.health_read=read;m.health_refresh()
        row=list(m.health_state['history']['mac:frame-mac-1'])[-1]
        self.assertEqual(len(row),20);self.assertEqual(row[19],[0,50]);self.assertEqual(row[8],0)
        self.assertEqual(row[9:15],[100,200,20,800,10,20])
        host=list(m.health_state['history']['host:1'])[-1]
        self.assertEqual(host[9:11],[20,800])
        resources['sample_at']=now-60;m.health_refresh()
        row=list(m.health_state['history']['mac:frame-mac-1'])[-1]
        self.assertIsNone(row[4]);self.assertIsNone(row[8]);self.assertIsNone(row[19])

    def test_replica_workloads_are_not_host_or_other_app_resources(self):
        self.state['data']['projects']=[{'name':'frame','placed':[{'machine':1,'replica':1},{'machine':1,'replica':2}]}]
        self.sample['replicas']={'frame':{'app':'frame','replica':1,'values':{'cpu':125,'mem':100,'netRx':0}},
                                 'frame-r2':{'app':'frame','replica':2,'values':{'cpu':25,'mem':30,'diskWrite':20}},
                                 'dosa':{'app':'dosa','replica':1,'values':{'cpu':700,'mem':999}}}
        w=self.node()['workloads']
        self.assertEqual([x['cpu_cores'] for x in w],[1.25,.25])
        self.assertEqual([x['memory_bytes'] for x in w],[100,30])
        self.assertEqual(w[0]['network_rx'],0)
        self.assertEqual(w[0]['state'],'healthy')
        self.assertEqual(w[1]['state'],'starting')
        self.sample['run']='old-run'
        self.assertTrue(all(x['cpu_cores'] is None for x in self.node()['workloads']))

    def test_app_aggregate_is_only_a_single_replica_fallback(self):
        self.sample['a']={'frame':{'cpu':175,'mem':400}}
        self.assertEqual(self.node()['workloads'][0]['cpu_cores'],1.75)
        self.state['data']['projects']=[{'name':'frame','placed':[{'machine':1,'replica':1},{'machine':1,'replica':2}]}]
        self.assertTrue(all(x['cpu_cores'] is None for x in self.node()['workloads']))

    def test_mac_telemetry_freshness_is_independent_of_reachable_service(self):
        source={'data':{'ok':True,'resources':{'sample_at':995,'cpu_percent':0,'memory_percent':25,'disk_free':100,
                                             'network_rx':0,'network_tx':float('nan'),'secret':'do-not-forward'}},'at':999}
        row=self.m.health_mac('frame-mac-1',source,self.now)
        self.assertTrue(row['online']);self.assertTrue(row['resource_metrics']);self.assertFalse(row['resource_stale'])
        self.assertEqual(row['cpu_percent'],0);self.assertEqual(row['network_rx'],0);self.assertIsNone(row['network_tx'])
        self.assertNotIn('do-not-forward',json.dumps(row))
        for update in ({'sample_at':950},{'sample_at':995,'error':'Collector failed'}):
            source['data']['resources'].update(update)
            row=self.m.health_mac('frame-mac-1',source,self.now)
            self.assertTrue(row['online']);self.assertTrue(row['resource_stale'])
        source['data'].pop('resources')
        row=self.m.health_mac('frame-mac-1',source,self.now)
        self.assertFalse(row['resource_metrics']);self.assertIsNone(row['cpu_percent'])

    def test_streaming_metrics_only_use_recent_work_and_live_leases(self):
        m=self.m;now=time.time();seen=time.monotonic()
        m.bstats['served'].extend([(now-400,1,'old',99),(now,2,'cache',.1),(now,3,'mac',.3)])
        m.bstats['vps'].extend([(now-400,10,9999999,True),(now,2,1000000,True)])
        m.playback_sessions.update(a={'closed':False,'seen':seen,'mode':'remux'},b={'closed':True,'seen':seen,'mode':'converted'},c={'closed':False,'seen':seen-m.PLAYBACK_TTL-1})
        m.src_trouble.update(active={'until':now+10},expired={'until':now-1})
        row=m.health_service()
        self.assertEqual(row['sessions'],1);self.assertEqual(row['modes'],{'remux':1})
        self.assertEqual(row['segment_samples'],2);self.assertEqual(row['segment_p50_ms'],200);self.assertEqual(row['segment_p95_ms'],300)
        self.assertEqual(row['source_fetch_mbps'],4);self.assertEqual(row['paused_sources'],1)


if __name__=='__main__':unittest.main()
