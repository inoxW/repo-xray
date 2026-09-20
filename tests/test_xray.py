import tempfile
from pathlib import Path
import unittest
from xray import analyze, render

class AnalyzerTests(unittest.TestCase):
    def project(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, content in files.items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding='utf-8')
        return root

    def test_relative_src_and_symbols(self):
        root = self.project({'src/pkg/__init__.py':'', 'src/pkg/a.py':'from . import b\nfrom .b import run\nimport json\nif __name__ == "__main__":\n    run()','src/pkg/b.py':'def run(): pass'})
        data = analyze(root)
        self.assertIn({'source':'src/pkg/a.py','target':'src/pkg/b.py'}, data['edges'])
        a = next(n for n in data['nodes'] if n['id'].endswith('a.py'))
        self.assertTrue(a['entry'])
        self.assertEqual(a['external'], ['json'])

    def test_cycle_and_syntax_error(self):
        data = analyze(self.project({'a.py':'import b','b.py':'import a','bad.py':'def !'}))
        self.assertEqual(len(data['edges']), 2)
        self.assertEqual(len(data['warnings']), 1)

    def test_does_not_execute(self):
        root = self.project({'unsafe.py':'raise RuntimeError("never execute")'})
        self.assertEqual(len(analyze(root)['nodes']), 1)

    def test_script_escape(self):
        root = self.project({'x.py':'x="</script><script>alert(1)</script>"'})
        render(analyze(root), root/'out.html')
        text = (root/'out.html').read_text()
        self.assertNotIn('</script><script>alert(1)', text)
        self.assertIn('\\u003c/script', text)

    def test_symlink_and_excluded_directory(self):
        root = self.project({'main.py':'pass','.venv/hidden.py':'pass'})
        (root/'link.py').symlink_to(root/'main.py')
        self.assertEqual([n['id'] for n in analyze(root)['nodes']], ['main.py'])

    def test_parent_relative(self):
        root = self.project({'pkg/__init__.py':'','pkg/util.py':'VALUE=1','pkg/sub/__init__.py':'','pkg/sub/main.py':'from ..util import VALUE'})
        self.assertIn({'source':'pkg/sub/main.py','target':'pkg/util.py'},analyze(root)['edges'])

if __name__ == '__main__':
    unittest.main()
