# Web frontend

Vue 3、TypeScript 和 Vite。`src/pages/` 覆盖节点门户、用户中心、后台、反馈、诊断、开放文档和首次初始化；`src/site-pages/` 覆盖官网和下载站。

```sh
npm ci
npm run build
npm test
```

节点产物写入 `../web/tunnel/web/`，官网和下载站产物写入 `../site/`。`pages.json` 和 `pages.site.json` 定义入口。所有页面使用完整 Vue 组件，服务端保留首屏 HTML，原有 CSS 布局和访问路径保持一致。

构建官网时，根据已注册页面的 canonical 地址，按域名自动生成各自的 `sitemap.xml` 和 `robots.txt`。下载服务在运行时生成公开下载站地图，公开版本变更后自动更新。

接口交互模块经 Vite 编译并接入 Vue 生命周期，请求、监听和计时器会随页面销毁回收。新增布局请修改对应 Vue 组件，同时维护服务端首屏 HTML，不要手工修改生成的 `vue-*.js`。
