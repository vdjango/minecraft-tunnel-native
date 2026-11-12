"""Generate/check method and DOM inventories from authoritative source ASTs.

Python uses ast, C/C++ uses tree-sitter, TS/JS/Vue uses the frontend's pinned
parsers. The inventory is a search aid, not proof of absence of duplicated logic.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def python_symbols(path: Path) -> list[dict]:
    rows = []
    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope = []

        def definition(self, node):
            symbol = '.'.join([*self.scope, node.name])
            rows.append(dict(path=path.relative_to(ROOT).as_posix(), line=node.lineno,
                             kind=type(node).__name__, symbol=symbol))
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = definition

        def visit_Lambda(self, node):
            rows.append(dict(path=path.relative_to(ROOT).as_posix(), line=node.lineno,
                             kind='Lambda', symbol='.'.join([*self.scope, f'<lambda@{node.lineno}>'])))
            self.generic_visit(node)
    Visitor().visit(ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path)))
    return rows


def cpp_symbols(path: Path, parser) -> list[dict]:
    source = path.read_bytes()
    rows = []
    def visit(node, scope):
        if node.type in ('namespace_definition', 'class_specifier', 'struct_specifier'):
            name = node.child_by_field_name('name')
            scope = [*scope, name.text.decode() if name else '<anonymous>']
        if node.type == 'function_definition':
            declarator = node.child_by_field_name('declarator')
            if declarator:
                symbol = ' '.join(declarator.text.decode().split())
                rows.append(dict(path=path.relative_to(ROOT).as_posix(), line=node.start_point.row + 1,
                                 kind='definition', symbol='::'.join([*scope, symbol])))
                scope = [*scope, symbol]
        elif node.type == 'lambda_expression':
            rows.append(dict(path=path.relative_to(ROOT).as_posix(), line=node.start_point.row + 1,
                             kind='lambda', symbol='::'.join([*scope, f'<lambda@{node.start_point.row + 1}>'])))
        elif node.type in ('declaration', 'field_declaration'):
            # Function declarations only; don't mistake function pointer variables
            # or calls within an initializer for method definitions.
            decl = node.child_by_field_name('declarator')
            if decl and decl.type == 'function_declarator':
                rows.append(dict(path=path.relative_to(ROOT).as_posix(), line=node.start_point.row + 1,
                                 kind='declaration', symbol='::'.join([*scope, ' '.join(decl.text.decode().split())])))
        for child in node.named_children:
            visit(child, scope)
    visit(parser.parse(source).root_node, [])
    return rows


class ShellDom(HTMLParser):
    def __init__(self, path):
        super().__init__()
        self.path, self.rows = path, []

    def handle_starttag(self, tag, attrs):
        attributes = {key: value or '' for key, value in attrs if key in ('id', 'class', 'role') or key.startswith(('aria-', 'data-'))}
        self.rows.append(dict(path=self.path, line=self.getpos()[0], tag=tag, attributes=attributes, directives=[]))

    handle_startendtag = handle_starttag


def build() -> dict[str, str]:
    from tree_sitter import Language, Parser
    import tree_sitter_cpp
    parser = Parser(Language(tree_sitter_cpp.language()))
    methods, sources = [], []
    for directory in ('core', 'bridge', 'include', 'bindings', 'web', 'scripts'):
        for path in sorted((ROOT / directory).rglob('*')):
            if '__pycache__' in path.parts or not path.is_file():
                continue
            if path.suffix == '.py':
                methods.extend(python_symbols(path))
            elif path.suffix in ('.h', '.hpp', '.cpp', '.c'):
                methods.extend(cpp_symbols(path, parser))
            else:
                continue
            sources.append(path.relative_to(ROOT).as_posix())
    frontend = json.loads(subprocess.check_output(['node', str(ROOT / 'frontend/scripts/code-inventory.mjs')], cwd=ROOT, text=True, encoding='utf-8'))
    methods.extend(frontend['methods'])
    sources.extend(frontend['files'])
    dom = frontend['dom']
    for path in sorted((ROOT / 'web/tunnel/web').glob('*.html')):
        scanner = ShellDom(path.relative_to(ROOT).as_posix())
        scanner.feed(path.read_text(encoding='utf-8-sig'))
        dom.extend(scanner.rows)
        sources.append(path.relative_to(ROOT).as_posix())
    # Hashes detect method bodies, DOM directives and removals even if names stay.
    manifest = {name: hashlib.sha256((ROOT / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for name in sorted(set(sources))}
    order = lambda row: (row['path'], row['line'], row.get('symbol', row.get('tag', '')))
    data = {
        'methods.json': dict(schema=1, methods=sorted(methods, key=order)),
        'dom.json': dict(schema=1, elements=sorted(dom, key=order), selectors=sorted(frontend['selectors'], key=lambda row: (row['path'], row['line']))),
        'sources.json': dict(schema=1, authoritative_sources=manifest),
    }
    return {name: json.dumps(value, ensure_ascii=False, indent=2) + '\n' for name, value in data.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    destination = ROOT / 'docs/90-代码清单'
    changed = []
    for name, content in build().items():
        path = destination / name
        if not path.exists() or path.read_text(encoding='utf-8') != content:
            changed.append(name)
            if not args.check:
                destination.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8', newline='\n')
    print(('STALE ' if args.check and changed else 'OK ') + ', '.join(changed or ['inventory current']))
    return 1 if args.check and changed else 0


if __name__ == '__main__':
    sys.exit(main())
