"""Validate numbered documentation, local Markdown links and generated inventory paths."""
from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'!?\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+"[^"]*")?\)')


def main():
    errors = []
    documents = sorted((ROOT / 'docs').rglob('*.md'))
    sources = documents + [ROOT / name for name in ('README.md', 'AGENTS.md', 'PROTOCOL_STANDARD.md',
                                                  '.agents/skills/multiplayer-native/SKILL.md')]
    referenced = set()
    for path in sources:
        text = path.read_text(encoding='utf-8-sig')
        text = re.sub(r'^(```|~~~)[\s\S]*?^\1[^\n]*$', '', text, flags=re.MULTILINE)
        for match in LINK.finditer(text):
            target = unquote(match[1].strip('<>').split('#', 1)[0])
            if not target or re.match(r'[a-zA-Z][\w+.-]*:', target) or target.startswith('/'):
                continue
            destination = (path.parent / target).resolve()
            if not destination.exists():
                errors.append(f'{path.relative_to(ROOT)}: missing {target}')
            referenced.add(destination)
    for path in documents:
        if path.parent.name != '60-版本发布' and not re.match(r'^\d{2}-.+\.md$', path.name):
            errors.append(f'{path.relative_to(ROOT)}: expected NN-title.md')
        if path.resolve() not in referenced:
            errors.append(f'{path.relative_to(ROOT)}: no navigation link')
    for name in ('methods', 'dom', 'sources'):
        if not (ROOT / 'docs/90-代码清单' / (name + '.json')).is_file():
            errors.append(f'Missing generated inventory: {name}.json')
    prefix = 'https://github.com/vdjango/minecraft-tunnel-native/blob/main/'
    for path in (ROOT / 'frontend/src').rglob('*.vue'):
        for target in re.findall(r'href="(' + re.escape(prefix) + r'[^"#]+)', path.read_text(encoding='utf-8')):
            if not (ROOT / unquote(target.removeprefix(prefix))).is_file():
                errors.append(f'{path.relative_to(ROOT)}: missing repository link {target}')
    workflow = (ROOT / '.github/workflows/core.yml').read_text(encoding='utf-8')
    if 'docs/60-版本发布/$RELEASE_TAG.md' not in workflow:
        errors.append('Release workflow does not use numbered release directory')
    if errors:
        print('\n'.join(errors))
        return 1
    print(f'OK {len(documents)} numbered documents; local links, navigation and release paths valid')
    return 0


if __name__ == '__main__':
    sys.exit(main())
