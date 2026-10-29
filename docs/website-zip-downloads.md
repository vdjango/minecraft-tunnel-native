# 启动器网站 ZIP 下载

下载站的 Windows、Linux 按钮及历史版本只展示 ZIP，macOS 沿用已有应用 ZIP。每个平台和架构只显示一个文件；没有 ZIP 时不退回 EXE 下载。页面说明解压后运行程序。

## 发布流程

1. 启动器原有 CI 继续产出各平台程序及 SHA-256 校验文件，不改版本号或工作流。
2. 独立发布后台 `buggcraft-release-platform` 校验原始产物后，在工作线程生成网站 ZIP。Windows 为 `Buggcraft-windows-x64.zip`，Linux 为 `Buggcraft-linux-x86_64.zip`、`Buggcraft-linux-arm64.zip`，校验文件为 `SHA256SUMS-downloads.txt`。
3. 新文件与版本记录一起入库，遵循原有审核、发布、撤回、七牛镜像流程。已入库版本在服务启动时补齐网站 ZIP。
4. 网站控制器优先读取后台公开列表；冻结的 `release-history.json` 也必须先补齐 ZIP 再部署页面。历史 ZIP 放在网站的 `browser-packages/{tag}/`，避开旧 GitHub 缓存的定时清理目录 `release-cache/`。历史原文件保留，避免破坏旧链接；旧缓存同步任务写入 `releases.json`，不会重写冻结的历史列表。

应用内更新仍通过原来的 `/api/launcher/update`、原始产物和票据接口分发。Windows 更新继续下载 EXE，Linux 更新继续下载原程序；不把网站 ZIP 用于应用内更新。

网站入口控制器：`site/download-page.js`；Vue 页面：`frontend/src/site-pages/download.vue`。修改后执行 `npx vite build --config vite.site.config.ts`，部署生成的下载页及其 JS 依赖，避免只改源码而网站没有生效。

## 回归验证

- 发布后台：`python -m unittest discover -s tests -q`，验证 ZIP 内容、校验值、Linux 权限、GET/HEAD/Range、旧版本补齐、审核撤回，以及五个平台的原始更新文件保持一致。
- 网站：在 `frontend` 执行 `npm test`，`tests/site-download.test.ts` 实际点击平台和架构按钮，检查 ZIP 下载、校验链接、历史版本去重、版本直达链接，以及禁止回退 EXE。
- 上线：逐个平台记录并比较更新接口前后结果，实际下载 ZIP 并核对内部程序与原始更新产物的 SHA-256；检查历史版本和七牛下载可用。
