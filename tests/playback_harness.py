"""Run the real media HTTP backend locally without its unrelated torrent daemon."""
import ast
import os
from pathlib import Path
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]


def backend(path=None):
    path = path or ROOT / 'runners/tor.py'
    tree = ast.parse(path.read_text())
    tree.body = [n for n in tree.body if not (
        isinstance(n, ast.Import) and any(a.name == 'libtorrent' for a in n.names)
        or isinstance(n, ast.Assign) and any(getattr(t, 'id', '') in ('ses', 'DELETE') for t in n.targets))]
    mod = types.ModuleType('playback_test_backend')
    mod.__file__ = str(path)
    exec(compile(tree, str(path), 'exec'), mod.__dict__)
    mod.SEG_DIR = tempfile.mkdtemp(prefix='frame-media-test-')
    mod.reader_of = lambda key: 1
    mod.claim_reader = lambda key: 1
    mod.ask_copies = lambda *a: (None, None)
    mod.facts_keep = lambda *a: None
    mod.converters = lambda *a: [1]
    return mod
