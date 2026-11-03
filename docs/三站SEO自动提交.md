# Buggcraft 三站 SEO 与自动提交

官网、镜像源、联机节点和译文库是同一产品的不同服务。首页和公开文档使用各自的标题、描述与 canonical；Vue 构建会从同一组件生成首屏内容。节点和官网站点地图随构建生成，镜像与译文站点地图由服务端提供。公开页互相链接，账号、后台、API、下载文件和临时文件不进入站点地图。

服务器上的 SEO 提交脚本由 systemd 定时器每日北京时间 02、08、14、20 点运行，增加最多 15 分钟随机错峰。单次最多检查 64 页，最多四路并发；页面内容变化才重新提交，失败单独退避重试。任务报告位于 /var/lib/buggcraft-seo/latest-report.json，运行状态可用 systemctl status buggcraft-seo-submit.timer 查看。

每站各有独立 IndexNow 验证文件。IndexNow 返回 200 表示接收 URL，202 表示所有权验证仍在进行；两者都不等于页面已经被搜索引擎收录。Google 的普通网页使用 Search Console 站点地图提交接口，需站点所有权与 OAuth 授权；百度需该站点在资源平台提供的提交地址和令牌。缺少授权时报告显示 authorization_required，不会伪造提交成功。配置文件只在服务器 /etc/buggcraft-seo/config.json，权限 0600，不提交到仓库。

接入 Google 时，在各站点配置对象下填写 google.site_url（Search Console 已验证的网址属性）、google.client_id、google.client_secret、google.refresh_token；接入百度时填写该站点的 baidu.submit_url。示例配置见 scripts/seo_submit.example.json，真实凭据勿写入仓库。部署只更新 SEO 页面和提交任务，节点及镜像下载服务不重启。

2026-11-03 首次提交：官网 1、镜像源 3、联机节点 4 个 URL 收到 IndexNow 202；单独复核时官网、镜像源、译文库首页收到 200，节点收到 202。译文库 678 个候选中有一个检查超时，另外 677 个通过规范地址和正文检查并收到 IndexNow 200。仍须在各搜索引擎站长平台观察实际收录。Google、百度授权待配置。
