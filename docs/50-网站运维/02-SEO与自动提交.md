# Buggcraft 三站 SEO 与自动提交

官网、镜像源、联机节点和译文库是同一产品的不同服务。首页和公开文档使用各自的标题、描述与 canonical；Vue 构建会从同一组件生成首屏内容。节点和官网站点地图随构建生成，镜像与译文站点地图由服务端提供。公开页互相链接，账号、后台、API、下载文件和临时文件不进入站点地图。

下载站也已加入自动提交站点，公开首页使用 `https://download.bugg.icu/`，站点地图为 `https://download.bugg.icu/sitemap.xml`；实际安装文件不作为网页提交。

服务器上的 SEO 提交脚本由 systemd 定时器每日北京时间 02、08、14、20 点运行，增加最多 15 分钟随机错峰。单次最多检查 64 页，最多四路并发；页面内容变化才重新提交，失败单独退避重试。任务报告位于 /var/lib/buggcraft-seo/latest-report.json，运行状态可用 systemctl status buggcraft-seo-submit.timer 查看。

每站各有独立 IndexNow 验证文件。IndexNow 返回 200 表示接收 URL，202 表示所有权验证仍在进行；两者都不等于页面已经被搜索引擎收录。Google 的普通网页使用 Search Console 站点地图提交接口，需站点所有权与 OAuth 授权；百度需该站点在资源平台提供的提交地址和令牌。缺少授权时报告显示 authorization_required，不会伪造提交成功。配置文件只在服务器 /etc/buggcraft-seo/config.json，权限 0600，不提交到仓库。

接入 Google 时，在各站点配置对象下填写 google.site_url（Search Console 已验证的网址属性）、google.client_id、google.client_secret、google.refresh_token；接入百度时填写该站点的 baidu.submit_url。示例配置见 scripts/seo_submit.example.json，真实凭据勿写入仓库。部署只更新 SEO 页面和提交任务，节点及镜像下载服务不重启。

官网普通收录使用百度 API 主动提交，站点地图地址为 `https://bugg.icu/sitemap.xml`，内容是直接列出页面的 `urlset`，不是索引型 Sitemap。只提交最终 HTTPS 规范地址，不提交跳转入口。API 与手动提交共享日配额；Sitemap 的配额独立，后台显示 Sitemap 余额为 0 不代表 API 余额也为 0。API 返回 `over quota` 时保留未提交页面，等待北京时间次日额度恢复后由定时器继续，不将其记录为已提交。

<<<<<<< HEAD:docs/50-网站运维/02-SEO与自动提交.md
## 历史回执（不是当前授权或配额状态）

2026-11-03 首次提交：官网 1、镜像源 3、联机节点 4 个 URL 收到 IndexNow 202；单独复核时官网、镜像源、译文库首页收到 200，节点收到 202。译文库 678 个候选中有一个检查超时，另外 677 个通过规范地址和正文检查并收到 IndexNow 200。当时 Google、百度授权待配置；后续状态见对应日期回执，实际收录仍需站长平台核实。
=======
2026-11-03 首次提交：官网 1、镜像源 3、联机节点 4 个 URL 收到 IndexNow 202；单独复核时官网、镜像源、译文库首页收到 200，节点收到 202。译文库 678 个候选中有一个检查超时，另外 677 个通过规范地址和正文检查并收到 IndexNow 200。仍须在各搜索引擎站长平台观察实际收录。Google、百度授权待配置。
>>>>>>> origin/main:docs/三站SEO自动提交.md

2026-11-06 更新官网百度提交凭据：12 个 Sitemap 页面均通过规范地址和正文检查；本次百度 API 接收 9 个，日余额为 0，另外 3 个保留在待提交状态。首页、Windows 和 macOS 启动排查教程已包含在本次成功批次中。批次报告位于 `/var/lib/buggcraft-seo/baidu-report.json`。同时修复此前人工写入状态文件造成的定时服务读取权限错误；人工维护状态文件时必须保留 StateDirectory 的属主，凭据仍保持根用户专有的 0600 权限。

同日接入下载站、镜像源站和联机节点的百度普通收录 API，分别接收 1、3、4 个页面，均无待补交页面。提交与配额结果记录在 `/var/lib/buggcraft-seo/baidu-all-sites-report.json`，成功记录已经合并到定时任务状态，后续仅推送新增或变化的页面。接口配置保留百度平台提供的原始 `site=https://域名` 参数形式；本次实测将整个 `site` 值进行百分号编码会收到 `site init fail`，改用平台给出的原始地址后接收成功。
