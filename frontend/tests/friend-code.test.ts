import {expect,it} from 'vitest';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {resolve} from 'node:path';
import {TextEncoder} from 'node:util';

it('web friend codes agree with the node and launcher identity vector',async()=>{
  const source=readFileSync(resolve(process.cwd(),'../web/tunnel/web/account-page.js'),'utf8');
  const helper=source.slice(source.indexOf('async function shortFriendCode'),source.indexOf('function friendRow'));
  const code=new Function('crypto','TextEncoder',helper+';return shortFriendCode;')(webcrypto,TextEncoder);
  expect(await code('a'.repeat(32)+'.'+'b'.repeat(32))).toBe('REAJ-WVMV-6ICI');
});
