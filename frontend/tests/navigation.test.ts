import {afterEach,expect,it,vi} from 'vitest';
import source from '../src/controllers/navigation.js?raw';

afterEach(()=>{document.body.replaceChildren();vi.unstubAllGlobals();});

it('tracks section anchors without treating the tutorials route as a CSS selector',()=>{
  document.body.innerHTML='<a data-section-nav href="#home">首页</a><a data-section-nav href="/docs">教程</a><a data-section-nav href="#missing">缺失</a><section id="home"></section>';
  const observe=vi.fn(),disconnect=vi.fn();
  let notify:IntersectionObserverCallback|undefined;
  vi.stubGlobal('IntersectionObserver',class {
    observe=observe;disconnect=disconnect;
    constructor(callback:IntersectionObserverCallback){notify=callback;}
  });
  let dispose:(()=>void)|undefined;
  expect(()=>new Function('scope',source)({onDispose:(callback:()=>void)=>{dispose=callback;}})).not.toThrow();
  expect(observe).toHaveBeenCalledExactlyOnceWith(document.getElementById('home'));
  notify!([{target:document.getElementById('home'),isIntersecting:true,boundingClientRect:{top:0}}] as unknown as IntersectionObserverEntry[],{} as IntersectionObserver);
  expect(document.querySelector('[href="#home"]')?.getAttribute('aria-current')).toBe('location');
  expect(document.querySelector('[href="/docs"]')?.getAttribute('href')).toBe('/docs');
  dispose!();expect(disconnect).toHaveBeenCalledOnce();
});
