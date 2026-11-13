#!/usr/bin/env python3
"""Update only Frame's inline Python config, keeping its existing image and volumes.

RUNNERS_ADMIN_PASSWORD is read from the environment. --expect-version prevents an
unnoticed concurrent deployment from being overwritten. --apply is explicit.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import re
import urllib.error
import urllib.request


def packed(source):
    value = base64.b85encode(lzma.compress(source, preset=6)).decode().replace('$', '.')
    lines = '\n'.join(value[n:n + 120] for n in range(0, len(value), 120))
    return ("# Packed Tor source: lzma and base85; dollar bytes escaped for Compose.\n"
            "import base64, lzma, runpy, sys\n"
            "with open('/tmp/tor.py', 'wb') as fh:\n"
            "    fh.write(lzma.decompress(base64.b85decode(\"\"\"\n" + lines +
            "\n\"\"\".translate({10: None, 46: 36}))))\n"
            "sys.argv[0] = '/tmp/tor.py'\n"
            "runpy.run_path('/tmp/tor.py', run_name='__main__')\n")


def compose_source(compose, source):
    pattern = r'(?m)(^configs:\n  code:[^\n]*\n    content: \|\n)(?:^ {6}[^\n]*\n|^\n)+'
    content = ''.join('      ' + line + '\n' for line in packed(source).splitlines())
    result, count = re.subn(pattern, lambda m: m[1] + content, compose)
    if count != 1:
        raise ValueError('Expected one inline configs.code.content block; no update made')
    revision = hashlib.sha256(source).hexdigest()[:12]
    result, count = re.subn(r'(?m)^(      TOR_CODE:) [^\n]*', lambda m: m[1] + ' "' + revision + '"', result)
    if count != 1:
        raise ValueError('Expected one TOR_CODE marker; no update made')
    return result, revision


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--url', default=os.environ.get('RUNNERS_URL', 'https://runners.billybishop4-workers.xyz'))
    ap.add_argument('--app', default='frame')
    ap.add_argument('--expect-version', required=True, type=int)
    ap.add_argument('--source', type=Path, default=Path(__file__).with_name('tor.py'))
    ap.add_argument('--mode', choices=['source', 'legacy'], default='legacy')
    ap.add_argument('--cold-priority-ms', type=int, help='Optional foreground priority window, 0–1000 ms')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    if args.cold_priority_ms is not None and not 0 <= args.cold_priority_ms <= 1000:
        ap.error('--cold-priority-ms must be between 0 and 1000')
    if not re.fullmatch(r'[a-z0-9-]+', args.app):
        ap.error('invalid app name')
    headers = {'User-Agent': 'tor-source-deploy/1.0'}
    url = args.url.rstrip('/') + '/api/projects/' + args.app
    project = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30))
    if project['version'] != args.expect_version:
        ap.error('app version changed; inspect the current deployment before retrying')
    source = args.source.read_bytes()
    compile(source, str(args.source), 'exec')
    builder = Path(__file__).with_name('web') / 'build.py'
    if builder.is_file() and args.source.resolve() == Path(__file__).with_name('tor.py').resolve():
        module_spec = importlib.util.spec_from_file_location('tor_web_build', builder)
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        if module.bundle(source.decode()) != source.decode():
            ap.error('web sources changed; run python3 runners/web/build.py before deploying')
    compose, revision = compose_source(project['compose'], source)
    spec = {k: project[k] for k in ('files', 'port', 'replicas', 'region', 'regions', 'spreadTags') if k in project}
    spec.update(compose=compose, env={'MAC_INPUT_MODE': args.mode})
    if args.cold_priority_ms is not None:
        spec['env']['TOR_COLD_PRIORITY_MS'] = str(args.cold_priority_ms)
    if not args.apply:
        print(json.dumps({'app': args.app, 'current_version': project['version'], 'code': revision,
                          'replicas': project['replicas'], 'mode': args.mode,
                          'cold_priority_ms': args.cold_priority_ms, 'applied': False}))
        return
    password = os.environ.get('RUNNERS_ADMIN_PASSWORD')
    if not password:
        ap.error('RUNNERS_ADMIN_PASSWORD must be set')
    headers.update({'x-admin-password': password, 'Content-Type': 'application/json'})
    try:
        response = json.load(urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(spec).encode(),
                           headers=headers, method='PUT'), timeout=45))
    except urllib.error.HTTPError as error:
        raise SystemExit(f'Deployment HTTP {error.code}; response omitted to protect credentials') from None
    print(json.dumps({k: response.get(k) for k in ('name', 'version', 'stable', 'replicas', 'state')}))


if __name__ == '__main__':
    main()
