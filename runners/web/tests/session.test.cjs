const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../session.js'), 'utf8');

function setup() {
  const calls = [], timers = new Map(), saved = {}; let next = 0;
  const p = {url: '/hls/token/orig.m3u8', from: 18, native: true};
  const ctx = {URL, URLSearchParams, AbortController, Set, Date, Number, playing: p,
    crypto: {randomUUID: () => 'abcdef01-1234-1234-1234-123456789abc'},
    location: {href: 'https://frame.test/watch'}, window: {addEventListener() {}},
    navigator: {sendBeacon: u => { calls.push(['stop', u]); return true; }},
    setTimeout(fn) { timers.set(++next, fn); return next; }, clearTimeout(id) { timers.delete(id); },
    fetch: async (u, opts) => { calls.push(['fetch', u, opts]); return {ok: true, json: async () => ({})}; },
    vid: {paused: false, seeking: false, currentTime: 18}, isBuffered: () => false, bufferedAhead: () => 0,
    verId: (file, info) => file + '|' + (info || ''),
    store: {get: (key, fallback) => saved[key] || fallback, set: (key, value) => { saved[key] = value; }},
    byId: () => ({querySelector: () => ({})}), failPlay: (...a) => calls.push(['fail', ...a]),
    play: (...a) => { calls.push(['play', ...a]); ctx.playing = {}; },
  };
  vm.createContext(ctx); vm.runInContext(source, ctx);
  ctx.beginPlayback(p);
  return {ctx, p, calls, timers, saved};
}

test('start carries a fresh lease and resume position without waiting for a request', () => {
  const {p, calls} = setup(), u = new URL(p.mediaUrl);
  assert.equal(u.searchParams.get('start'), '18');
  assert.equal(u.searchParams.get('sid').length, 32);
  assert.equal(u.searchParams.get('managed'), '1');
  assert.equal(calls.length, 0);
});
test('switch releases once and aborts all pending work', () => {
  const {ctx, p, calls, timers} = setup(), controller = new AbortController();
  p.session.requests.add(controller);
  ctx.releasePlayback(p); ctx.releasePlayback(p);
  const stops = calls.filter(x => x[0] === 'fetch' && x[1].includes('action=stop'));
  assert.equal(stops.length, 1);
  assert.equal(stops[0][2].keepalive, true);
  assert.equal(stops[0][2].priority, 'high');
  assert.equal(calls.filter(x => x[0] === 'stop').length, 0);
  assert.equal(controller.signal.aborted, true);
  assert.equal(timers.size, 0);
});
test('a rejected foreground stop falls back to a beacon without reviving the lease', async () => {
  const {ctx, p, calls} = setup();
  ctx.fetch = async () => { throw Error('Page is leaving'); };
  ctx.releasePlayback(p);
  await Promise.resolve();
  assert.equal(calls.filter(x => x[0] === 'stop').length, 1);
  assert.equal(p.session.ended, true);
});
test('late diagnostics cannot change the replacement stream', async () => {
  const {ctx, p, calls} = setup(); let resolve;
  ctx.fetch = url => url.includes('action=stop') ? Promise.resolve({ok:true}) : new Promise(r => { resolve = r; });
  const poll = ctx.pollPlayback(p);
  ctx.releasePlayback(p); ctx.playing = {url: '/new'};
  resolve({ok: true, json: async () => ({fallback: 'index.m3u8', error: 'old failure'})});
  await poll;
  assert.equal(calls.filter(x => x[0] === 'play' || x[0] === 'fail').length, 0);
});
test('source outage is reported instead of encoding the same unavailable bytes', async () => {
  const {ctx, p, calls} = setup();
  ctx.fetch = async () => ({ok: true, json: async () => ({error: 'Source unavailable'})});
  await ctx.pollPlayback(p);
  assert.equal(calls.filter(x => x[0] === 'fail').length, 1);
  assert.equal(calls.filter(x => x[0] === 'play').length, 0);
});
test('a wrong file guess recovers through the same source token and can force encoding', () => {
  const {ctx, p, calls} = setup(); p.url = '/file/token/movie.mp4';
  assert.equal(ctx.recoverPlayback(p, 'decode failure', true), true);
  assert.equal(calls[0][1], 'https://frame.test/hls/token/index.m3u8?force=1');
});
test('recovery is bounded even after video already started', () => {
  const {ctx, p, calls} = setup(); p.started = true; p.fallbacks = 2;
  assert.equal(ctx.recoverPlayback(p, 'decode failure', true), false);
  assert.equal(calls.length, 0);
});
test('a successful correction skips the refused file on its next selection', () => {
  const {ctx, p, saved} = setup(); p.url = '/file/token/movie.mp4'; p.file = 'movie.mp4';
  ctx.recoverPlayback(p, 'decoder refused file', false);
  assert.equal(saved['playback-corrections-v1'], undefined, 'An attempted fallback proves nothing yet');
  const recovered = ctx.playing; recovered.url = '/hls/token/index.m3u8';
  ctx.rememberPlayback(recovered);
  assert.equal(saved['playback-corrections-v1'], undefined, 'Failed playback must not teach a correction');
  recovered.started = true; ctx.rememberPlayback(recovered);
  assert.equal(ctx.correctedPlaybackURL('/file/newtoken/movie.mp4', 'movie.mp4'), 'https://frame.test/hls/newtoken/index.m3u8');
  assert.equal(ctx.correctedPlaybackURL('/hls/newtoken/orig.m3u8', 'movie.mp4'), '/hls/newtoken/orig.m3u8', 'File failure does not blacklist Original');
});
test('a second successful fallback updates the original choice to require encoding', () => {
  const {ctx, p} = setup(); p.url = '/file/token/movie.mp4'; p.file = 'movie.mp4';
  ctx.recoverPlayback(p, 'file failed', false);
  const remux = ctx.playing;
  Object.assign(remux, {url:'/hls/token/index.m3u8', file:p.file, fallbacks:1, session:{ended:false}});
  ctx.recoverPlayback(remux, 'video needs encoding', true);
  Object.assign(ctx.playing, {url:'/hls/token/index.m3u8?force=1', started:true}); ctx.rememberPlayback(ctx.playing);
  assert.equal(ctx.correctedPlaybackURL(p.url, p.file), 'https://frame.test/hls/token/index.m3u8?force=1');
  assert.equal(ctx.correctedPlaybackURL(remux.url, p.file), 'https://frame.test/hls/token/index.m3u8?force=1');
});
test('old corrections expire and unrelated files retain their original route', () => {
  const {ctx, p, saved} = setup(); p.file = 'movie.mkv';
  const key = ctx.playbackChoiceKey(p.url, p.file);
  saved['playback-corrections-v1'] = {[key]:{force:true,at:Date.now() - 8 * 864e5}};
  assert.equal(ctx.correctedPlaybackURL(p.url, p.file), p.url);
  saved['playback-corrections-v1'][key].at = Date.now();
  assert.equal(ctx.correctedPlaybackURL(p.url, 'other.mkv'), p.url);
});
test('scrub bursts coalesce and buffered backward seeks report the new position', async () => {
  const {ctx, p, calls, timers} = setup();
  ctx.playbackSeek(p, 50); const first = p.session.seekTimer;
  ctx.playbackSeek(p, 80);
  assert.equal(timers.has(first), false);
  timers.get(p.session.seekTimer)(); await Promise.resolve();
  const sent = calls.find(x => x[0] === 'fetch');
  assert.match(sent[1], /at=80/); assert.match(sent[1], /seq=2/);
  assert.equal(sent[2].priority, 'high');
  ctx.isBuffered = () => true;
  ctx.playbackSeek(p, 10);
  timers.get(p.session.seekTimer)(); await Promise.resolve();
  assert.match(calls.filter(x => x[0] === 'fetch').at(-1)[1], /at=10&seq=3/);
});

test('progress carries the current seek sequence and is withheld during a seek', async () => {
  const {ctx, p, calls} = setup(); p.started = true; p.session.seq = 2;
  ctx.vid.currentTime = 85;
  await ctx.pollPlayback(p);
  let query = new URL(calls.find(x => x[0] === 'fetch')[1]).searchParams;
  assert.equal(query.get('at'), '85'); assert.equal(query.get('seq'), '2');
  ctx.vid.seeking = true;
  await ctx.pollPlayback(p);
  query = new URL(calls.filter(x => x[0] === 'fetch').at(-1)[1]).searchParams;
  assert.equal(query.has('at'), false);
});
