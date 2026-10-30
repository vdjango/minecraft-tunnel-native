import {describe, expect, it} from 'vitest';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';

const css = readFileSync(resolve('../web/tunnel/web/theme.css'), 'utf8');
const page = readFileSync(resolve('../web/tunnel/web/feedback.html'), 'utf8');

function rule(selector: string) {
  const start = css.indexOf(selector + '{');
  expect(start).toBeGreaterThanOrEqual(0);
  return css.slice(start + selector.length + 1, css.indexOf('}', start));
}

describe('feedback list layout', () => {
  it('keeps page spacing stable with the Vue wrapper present', () => {
    expect(rule('.shell')).toMatch(/margin:\s*0 auto/);
    expect(rule('.user-site .user-main')).toMatch(/flex:\s*1/);
    expect(css).not.toContain('.user-site>.user-main');
  });

  it('keeps a full page of issues in a bounded, scrollable area', () => {
    expect(rule('#issue-list')).toMatch(/max-height:\s*min\(/);
    expect(rule('#issue-list')).toMatch(/overflow-y:\s*auto/);
    expect(rule('#issue-list')).toMatch(/overflow-x:\s*hidden/);
    expect(page).toContain('id="issue-search"');
    expect(page).toContain('id="issues-prev"');
    expect(page).toContain('id="issues-next"');
    const revision = createHash('sha256').update(css).digest('hex').slice(0, 12);
    expect(page).toContain(`href="/theme.css?v=${revision}"`);
  });

  it('shows compact rows while keeping issue titles accessible', () => {
    expect(rule('#issue-list .issue-row')).toMatch(/padding:\s*11px 12px/);
    expect(rule('#issue-list .issue-row strong')).toMatch(/-webkit-line-clamp:\s*2/);
    expect(rule('#issue-list .issue-row:focus-visible')).toMatch(/outline:/);
    expect(css).toMatch(/@media\(max-width:760px\)\{#issue-list\{max-height:/);
  });
});
