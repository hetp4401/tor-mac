// Run with: node runners/web/tests/player.test.cjs (Node 14 or newer)
// These tests exercise media lifecycle decisions. Physical Safari fullscreen,
// pinch gestures and system PiP still require an Apple-device smoke test.
const assert = require('assert').strict;
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const cases = [];
const test = (name, run) => cases.push({name, run});
const source = fs.readFileSync(path.join(__dirname, '..', 'player.js'), 'utf8');

function declaration(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `Missing function ${name}`);
  const line = source.indexOf('\n', start);
  const end = source.slice(start, line).trimEnd().endsWith('}') ? line : source.indexOf('\n}', start) + 2;
  return source.slice(start, end);
}
function element() {
  const classes = new Set();
  return {
    classList: {add: (...names) => names.forEach(n => classes.add(n)), remove: (...names) => names.forEach(n => classes.delete(n)),
      contains: n => classes.has(n), toggle(n, on) { if (on) classes.add(n); else classes.delete(n); }},
    textContent: '', innerHTML: '', attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name] || null; },
    removeAttribute(name) { delete this.attributes[name]; },
    querySelectorAll: () => [], querySelector: () => element(), contains: () => false,
    addEventListener() {}, focus() {},
  };
}
function context(names, overrides = {}) {
  const nodes = new Map();
  const byId = id => { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); };
  const calls = [], queued = [];
  const vid = Object.assign(byId('vid'), {
    currentTime: 0, paused: true, readyState: 0, canPlayType: () => 'probably',
    play() { calls.push('play'); return Promise.resolve(); }, pause() {},
  });
  const ctx = {
    byId, vid, pl: byId('v-player'), playing: null, mediaFor: null, hls: null, calls, queued,
    window: {MediaSource: function() {}}, document: {activeElement: null, body: element()}, navigator: {userActivation: {isActive: true}},
    performance: {now: () => 1000}, Date, Promise, Set,
    setTimeout: fn => { queued.push(fn); return queued.length; }, clearTimeout() {}, requestAnimationFrame: fn => queued.push(fn),
    applePlayer: () => true, appleFiles: new Set(), rate: 1, nexting: false, upnT: 0, restartT: 0,
    posStore: () => ({}), posKey: () => 'test', kindLabel: x => x, sizeLine: x => x, fileName: x => x,
    WHY: {}, RISE: [], cur: {id: 'test', name: 'Test'}, curEp: null, layoutDirty: false,
    closeMenu() {}, ripOff() {}, hudOff() {}, hideUpNext() {}, paintCover() {}, stopBar() {}, placeBar() {},
    closeSheet() {}, paintTitles() {}, setUi() {}, setBusy() {}, showPlayer() {}, anim() {}, playerLayout() {},
    paintNativeOptions() {}, configureNativePlayer() {}, enterNativeFullscreen() {},
    teardown() { ctx.mediaFor = null; }, loadHls() { throw Error('Native HLS must not load hls.js'); },
    failPlay() { throw Error('Unexpected media failure'); },
    ...overrides,
  };
  vm.createContext(ctx);
  for (const name of names) vm.runInContext(declaration(name), ctx);
  return ctx;
}

test('Apple converted HLS attaches and plays in the source click task', () => {
  const ctx = context(['play', 'attach', 'startPlay']);
  ctx.play('/hls/test/index.m3u8', 'converted', 'film.mkv', '2 GB');
  assert.equal(ctx.vid.src, '/hls/test/index.m3u8');
  assert.equal(ctx.mediaFor, ctx.playing);
  assert.deepEqual(ctx.calls, ['play']);
  assert.ok(ctx.queued.length > 0, 'Secondary UI work remains deferred');
});

test('Apple original HLS and direct files also retain synchronous playback', () => {
  for (const url of ['/hls/test/orig.m3u8', '/test.mp4']) {
    const ctx = context(['play', 'attach', 'startPlay']);
    ctx.play(url, 'original', 'film.mkv', '2 GB');
    assert.equal(ctx.vid.src, url);
    assert.deepEqual(ctx.calls, ['play']);
  }
});

test('A superseded attachment cannot replace the current stream', () => {
  const ctx = context(['attach']);
  ctx.playing = {url: '/new.mp4'};
  ctx.vid.src = '/new.mp4';
  ctx.attach({url: '/old.mp4'});
  assert.equal(ctx.vid.src, '/new.mp4');
  assert.deepEqual(ctx.calls, []);
});

test('Native mode exposes browser controls and permits iPhone system playback', () => {
  const ctx = context(['configureNativePlayer'], {nativePlayer: () => true});
  ctx.vid.setAttribute('playsinline', 'true');
  ctx.vid.setAttribute('webkit-playsinline', 'true');
  ctx.configureNativePlayer();
  assert.equal(ctx.vid.controls, true);
  assert.equal(ctx.vid.playsInline, false);
  assert.equal(ctx.vid.getAttribute('playsinline'), null);
  assert.equal(ctx.vid.getAttribute('webkit-playsinline'), null);
  assert.equal(ctx.pl.classList.contains('native-player'), true);
});

test('Fullscreen targets the media element and provides an actionable rejection', async () => {
  const notices = [];
  const ctx = context(['enterNativeFullscreen'], {playing: {}, nativeFullscreen: () => false, inPip: () => false, nativeNotice: text => notices.push(text)});
  ctx.vid.requestFullscreen = function() { assert.equal(this, ctx.vid); ctx.calls.push('fullscreen'); return Promise.reject(Error('Activation expired')); };
  ctx.enterNativeFullscreen(true);
  await Promise.resolve();
  assert.deepEqual(ctx.calls, ['fullscreen']);
  assert.deepEqual(notices, ['Tap Full screen to open the system player.']);
});

test('Automatic fullscreen never attempts to reuse expired user activation', () => {
  const ctx = context(['enterNativeFullscreen'], {playing: {}, nativeFullscreen: () => false, inPip: () => false});
  ctx.navigator.userActivation.isActive = false;
  ctx.vid.requestFullscreen = () => { throw Error('Must not be called'); };
  ctx.enterNativeFullscreen(true);
});

test('PiP capability is refreshed after metadata and tolerates unsupported APIs', () => {
  const ctx = context(['canPictureInPicture']);
  ctx.vid.webkitSupportsPresentationMode = () => ctx.vid.readyState > 0;
  assert.equal(ctx.canPictureInPicture(), false);
  ctx.vid.readyState = 1;
  assert.equal(ctx.canPictureInPicture(), true);
  ctx.vid.webkitSupportsPresentationMode = () => { throw Error('Unsupported'); };
  assert.equal(ctx.canPictureInPicture(), false);
});

test('Autoplay denial keeps the stream and offers a user-gesture Play button', async () => {
  const ctx = context(['startPlay'], {nativeNotice: () => {}});
  ctx.playing = {native: true};
  ctx.vid.src = '/hls/test/index.m3u8';
  ctx.byId('pNativePlay').classList.add('hidden');
  ctx.vid.play = () => Promise.reject(Object.assign(Error('Needs a gesture'), {name: 'NotAllowedError'}));
  ctx.startPlay(ctx.playing);
  await Promise.resolve();
  assert.equal(ctx.byId('pNativePlay').classList.contains('hidden'), false);
  assert.equal(ctx.vid.src, '/hls/test/index.m3u8');
});

test('Returning from system PiP restores the existing session without reattachment', () => {
  const ctx = context(['paintWhere', 'inPip'], {
    playing: {url: '/film.mp4'}, wasPip: true, GLYPH: {pip: '', pipExit: '', airplay: ''}, paintFull() {},
    go: url => ctx.calls.push(url),
  });
  ctx.pl.classList.add('hidden');
  ctx.vid.src = '/film.mp4';
  ctx.paintWhere();
  assert.deepEqual(ctx.calls, ['#/play']);
  assert.equal(ctx.vid.src, '/film.mp4');
  assert.equal(ctx.byId('pPipReturn').classList.contains('hidden'), true);
});

test('Library navigation preserves a PiP stream and stops a regular session', () => {
  for (const pip of [true, false]) {
    const ctx = context(['route'], {
      playing: {}, navIntent: null, lastView: 'v-home', lastKey: 'play', window: {scrollY: 0},
      location: {hash: '#/library'}, URLSearchParams, inPip: () => pip,
      stopPlayer: () => ctx.calls.push('stop'),
      hidePlayer: () => { ctx.calls.push('hide'); throw Error('Reached library rendering'); },
    });
    assert.throws(() => ctx.route(false), /Reached library rendering/);
    assert.deepEqual(ctx.calls, pip ? ['hide'] : ['stop', 'hide']);
  }
});

(async () => {
  let failed = 0;
  for (const entry of cases) {
    try { await entry.run(); console.log('PASS ' + entry.name); }
    catch (error) { failed++; console.error('FAIL ' + entry.name + '\n' + error.stack); }
  }
  console.log(`${cases.length - failed}/${cases.length} player tests passed`);
  if (failed) process.exitCode = 1;
})();
