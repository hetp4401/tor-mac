"""The editable web app must remain a self-contained deployment artifact."""
import ast
import importlib.util
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('web_build', ROOT / 'runners/web/build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class WebBundleTests(unittest.TestCase):
    def test_source_page_fallback_matches_bundle(self):
        target = ROOT / 'runners/tor.py'
        tree = ast.parse(target.read_text())
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == 'watch_page')
        # Isolate the renderer without importing server startup or external clients.
        scope = {'WATCH_PAGE': None, '__file__': str(target), 'os': os}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(target), 'exec'), scope)
        self.assertEqual(scope['watch_page'](), build.page())
        scope['WATCH_PAGE'] = '<html>embedded deployment</html>'
        scope['__file__'] = '/missing/source/tor.py'
        self.assertEqual(scope['watch_page'](), scope['WATCH_PAGE'])

    def test_bundle_preserves_all_backend_code(self):
        original = (ROOT / 'runners/tor.py').read_text()
        result = build.bundle(original)
        def backend(value):
            tree = ast.parse(value)
            tree.body = [node for node in tree.body if not (
                isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], 'id', '') in ('WATCH_PAGE', 'WEB_FILES'))]
            return ast.dump(tree, include_attributes=False)
        self.assertEqual(backend(original), backend(result))

    def test_bundle_contains_current_page_and_assets(self):
        result = ast.parse(build.bundle((ROOT / 'runners/tor.py').read_text()))
        embedded = {node.targets[0].id: ast.literal_eval(node.value) for node in result.body
                    if isinstance(node, ast.Assign) and len(node.targets) == 1
                    and getattr(node.targets[0], 'id', '') in ('WATCH_PAGE', 'WEB_FILES')}
        self.assertEqual(embedded['WATCH_PAGE'], build.page())
        self.assertEqual(embedded['WEB_FILES'], build.assets())
        self.assertNotIn('<!-- TOR_PLAYER -->', embedded['WATCH_PAGE'])
        self.assertEqual(embedded['WATCH_PAGE'].count('<video '), 1)


if __name__ == '__main__':
    unittest.main()
