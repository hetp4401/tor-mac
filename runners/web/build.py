#!/usr/bin/env python3
"""Bundle the editable web sources into Tor's single-file deployment."""
import argparse
import ast
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT_PARTS = ('core.js', 'versions.js', 'player.js', 'pwa/client.js')
ASSETS = ('apple-touch-icon.png', 'icon-192.png', 'icon-512-maskable.png',
          'icon-512.png', 'manifest.webmanifest', 'sw.js')


def page():
    body = (ROOT / 'body.html').read_text()
    if body.count('<!-- TOR_PLAYER -->') != 1:
        raise ValueError('Expected one player placeholder')
    body = body.replace('<!-- TOR_PLAYER -->', (ROOT / 'player.html').read_text())
    scripts = ''.join((ROOT / name).read_text() for name in SCRIPT_PARTS)
    return ((ROOT / 'head.html').read_text() + body + '<script>' + scripts
            + '</script>\n</body>\n</html>\n')


def assets():
    return {name: base64.b64encode((ROOT / 'pwa' / name).read_bytes()).decode()
            for name in ASSETS}


def bundle(source):
    tree = ast.parse(source)
    values = {'WATCH_PAGE': page(), 'WEB_FILES': assets()}
    lines = source.splitlines(keepends=True)
    replacements = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        name = getattr(node.targets[0], 'id', None)
        if name not in values:
            continue
        value = values.pop(name)
        literal = (f'r"""{value}"""' if name == 'WATCH_PAGE' and '"""' not in value
                   else repr(value))
        replacements.append((node.lineno - 1, node.end_lineno, name + ' = ' + literal + '\n'))
    if values:
        raise ValueError('Missing embedded web constants: ' + ', '.join(values))
    for start, end, replacement in reversed(replacements):
        lines[start:end] = [replacement]
    result = ''.join(lines)
    compile(result, 'tor.py', 'exec')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Fail if embedded assets are stale')
    parser.add_argument('--html', type=Path, help='Also write a standalone preview HTML file')
    args = parser.parse_args()
    target = ROOT.parent / 'tor.py'
    old = target.read_text()
    new = bundle(old)
    if args.check and old != new:
        raise SystemExit('Embedded web assets are stale; run python3 runners/web/build.py')
    if not args.check and old != new:
        target.write_text(new)
    if args.html:
        args.html.write_text(page())
    print(json.dumps({'synchronized': old == new or not args.check,
                      'code': hashlib.sha256(new.encode()).hexdigest()[:12]}))


if __name__ == '__main__':
    main()
