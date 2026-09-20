"""Repo X-Ray: offline Python import explorer. Standard library only."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tokenize

SKIP = {'.git', '.venv', 'venv', '__pycache__', 'node_modules', 'build', 'dist', '.tox'}


def analyze(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('Project directory does not exist')
    files, warnings = [], []
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not Path(directory, d).is_symlink())
        for name in sorted(names):
            p = Path(directory, name)
            if p.suffix == '.py' and not p.is_symlink():
                files.append(p)
    if len(files) > 2000:
        raise ValueError('Maximum 2000 Python files; analyze a subdirectory instead')
    nodes, modules, trees = [], {}, {}
    for p in files:
        rel = p.relative_to(root).as_posix()
        parts = list(p.relative_to(root).with_suffix('').parts)
        if parts[0] == 'src':
            parts = parts[1:]
        package = p.name == '__init__.py'
        if package:
            parts = parts[:-1]
        module = '.'.join(parts)
        if module:
            modules.setdefault(module, []).append(rel)
        node = dict(id=rel, module=module, package=package, lines=0, symbols=[], entry=False, external=[], source='')
        nodes.append(node)
        try:
            if p.stat().st_size > 500_000:
                raise ValueError('File exceeds 500 KB')
            with tokenize.open(p) as f:
                code = f.read()
            node['source'] = code
            node['lines'] = len(code.splitlines())
            tree = ast.parse(code, filename=rel)
            trees[rel] = tree
            for item in ast.walk(tree):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    node['symbols'].append(dict(name=item.name, line=item.lineno, kind='class' if isinstance(item, ast.ClassDef) else 'function'))
                if isinstance(item, ast.If) and isinstance(item.test, ast.Compare):
                    t = item.test
                    if len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq):
                        pair = [t.left, t.comparators[0]]
                        if any(isinstance(x, ast.Name) and x.id == '__name__' for x in pair) and any(isinstance(x, ast.Constant) and x.value == '__main__' for x in pair):
                            node['entry'] = True
            node['entry'] |= p.name == '__main__.py'
        except (SyntaxError, UnicodeError, OSError, ValueError) as exc:
            warnings.append(f'{rel}: {exc}')
    edges = set()
    for node in nodes:
        tree = trees.get(node['id'])
        if tree is None:
            continue
        external = set()
        for item in ast.walk(tree):
            candidates = []
            if isinstance(item, ast.Import):
                candidates = [a.name for a in item.names]
            elif isinstance(item, ast.ImportFrom):
                base = item.module or ''
                if item.level:
                    package = node['module'].split('.') if node['package'] else node['module'].split('.')[:-1]
                    if item.level > len(package):
                        warnings.append(f"{node['id']}:{item.lineno}: unresolved relative import")
                        continue
                    base = '.'.join(package[:len(package) - item.level + 1] + ([base] if base else []))
                candidates = ([base] if base else []) + ['.'.join(filter(None, [base, a.name])) for a in item.names if a.name != '*']
            else:
                continue
            resolved = False
            for candidate in candidates:
                # A module can import a symbol; use its longest known module prefix.
                pieces = candidate.split('.')
                for end in range(len(pieces), 0, -1):
                    matches = modules.get('.'.join(pieces[:end]), [])
                    if matches:
                        resolved = True
                        if len(matches) > 1:
                            warnings.append(f"Ambiguous module: {candidate}")
                        for target in matches:
                            if target != node['id']:
                                edges.add((node['id'], target))
                        break
            if not resolved and candidates:
                external.add(candidates[0].split('.')[0])
        node['external'] = sorted(external)
    return dict(name=root.name, nodes=nodes, edges=[dict(source=a, target=b) for a, b in sorted(edges)], warnings=sorted(set(warnings)))


def render(data, output):
    template = Path(__file__).with_name('report.html').read_text(encoding='utf-8')
    payload = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    Path(output).write_text(template.replace('/*__DATA__*/null', payload), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', help='Local folder or https://github.com/OWNER/REPO')
    parser.add_argument('-o', '--output', default='xray-report.html')
    parser.add_argument('--json', dest='json_output', help='Optional JSON report path')
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix='repo-xray-') as tmp:
            project = args.project
            if project.startswith('https://'):
                if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?', project):
                    raise ValueError('Use a GitHub repository root URL')
                destination = str(Path(tmp, 'repository'))
                env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_LFS_SKIP_SMUDGE='1')
                subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', 'clone', '--depth', '1', '--', project, destination], check=True, timeout=120, env=env)
                data = analyze(destination)
                data['name'] = project.rstrip('/').split('/')[-1]
            else:
                data = analyze(project)
            render(data, args.output)
            if args.json_output:
                Path(args.json_output).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"Report: {args.output} | {len(data['nodes'])} files | {len(data['edges'])} imports | {len(data['warnings'])} warnings")
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
