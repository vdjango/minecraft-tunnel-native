(() => {
      const repository = 'https://github.com/vdjango/buggcraft';
      const releasesUrl = new URL('release-history.json', location.href).href;
      const managedReleasesUrl = 'https://download.bugg.icu/api/launcher/releases';
      const platforms = {
        windows: { title:'Windows', description:'Windows 10 / 11 · x64 · 解压 ZIP 后运行 Buggcraft.exe', assetNames:['Buggcraft-windows-x64.zip'], checksum:'SHA256SUMS-downloads.txt', fallback:/windows.*\.zip$/i },
        mac: { title:'macOS', description:'选择你的芯片类型，下载对应的 Buggcraft.app 压缩包。', checksums:{ arm64:'SHA256SUMS-macos-arm64.txt', x86_64:'SHA256SUMS-macos-x86_64.txt' }, fallback:null },
        linux: { title:'Linux', description:'选择处理器架构，下载对应的 ZIP 压缩包。', checksums:{ arm64:'SHA256SUMS-downloads.txt', x86_64:'SHA256SUMS-downloads.txt' } }
      };
      const archNames = { arm64:'Buggcraft-macos-arm64.zip', x86_64:'Buggcraft-macos-x86_64.zip' };
      const linuxArchNames = { arm64:'Buggcraft-linux-arm64.zip', x86_64:'Buggcraft-linux-x86_64.zip' };
      const root = document.documentElement;
      const query = new URLSearchParams(location.search);
      const requestedVersion = query.get('version') || '';
      let selectedPlatform = ['windows','mac','linux'].includes(query.get('platform')) ? query.get('platform') : detectPlatform();
      const requestedArch = (query.get('arch') || '').replace(/^(linux|macos)-/, '');
      let selectedArch = ['arm64','x86_64'].includes(requestedArch) ? requestedArch : detectArch();
      let releases = [];
      let shownHistory = 8;

      function detectPlatform() {
        return window.BuggcraftPlatform?.detect() || 'windows';
      }
      function detectArch() {
        const architecture = navigator.userAgentData?.architecture || '';
        if (/arm|aarch64/i.test(architecture + ' ' + navigator.userAgent)) return 'arm64';
        if (/x86|amd|intel/i.test(architecture)) return 'x86_64';
        return selectedPlatform === 'mac' ? 'arm64' : 'x86_64';
      }
      function dateText(value) {
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? '日期未提供' : new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'short',day:'numeric'}).format(date);
      }
      function dateTimeText(value) {
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? '时间未知' : new Intl.DateTimeFormat('zh-CN',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(date);
      }
      function asset(release, name, fallback) {
        const list = Array.isArray(release.assets) ? release.assets : [];
        for (const candidate of (Array.isArray(name) ? name : name ? [name] : [])) {
          const exact = list.find(item => item.name === candidate);
          if (exact) return exact;
        }
        return fallback ? list.find(item => fallback.test(item.name)) || null : null;
      }
      function platformAsset(release, platform, arch) {
        if (platform === 'mac') return asset(release, archNames[arch], new RegExp(`macos[-_].*${arch}.*\\.zip$`,'i'));
        if (platform === 'linux') return asset(release, linuxArchNames[arch]);
        const config = platforms[platform];
        return asset(release, config.assetNames, config.fallback);
      }
      function mostRecentFor(platform, arch) {
        if (requestedVersion) {
          const requested = releases.find(item => (item.tag_name || '').replace(/^v/, '') === requestedVersion.replace(/^v/, ''));
          const file = requested && platformAsset(requested, platform, arch);
          if (file) return {release:requested,file};
        }
        for (const release of releases) {
          const file = platformAsset(release,platform,arch);
          if (file) return {release,file};
        }
        return null;
      }
      function setLink(element, url, enabledText, disabledText) {
        if (!url) { element.href='#'; element.setAttribute('aria-disabled','true'); element.textContent=disabledText; return; }
        element.href=url; element.removeAttribute('aria-disabled'); element.textContent=enabledText;
      }
      function selectPlatform(platform, updateUrl=true) {
        selectedPlatform=platform;
        for (const button of document.querySelectorAll('.platform-tab')) button.setAttribute('aria-pressed',String(button.dataset.platform===platform));
        const config=platforms[platform];
        document.getElementById('platform-title').textContent=config.title;
        renderArchitecture();
        document.getElementById('detected-pill').textContent=platform===detectPlatform()?'与你的系统相符':'已切换平台';
        if(updateUrl){const url=new URL(location.href);url.searchParams.set('platform',platform);if(platform==='mac'||platform==='linux')url.searchParams.set('arch',selectedArch);else url.searchParams.delete('arch');history.replaceState(null,'',url);}
        renderSelectedRelease();
      }
      function selectArch(arch,updateUrl=true) {
        selectedArch=arch;
        renderArchitecture();
        if(updateUrl){const url=new URL(location.href);url.searchParams.set('platform',selectedPlatform);url.searchParams.set('arch',arch);history.replaceState(null,'',url);}
        renderSelectedRelease();
      }
      function renderArchitecture() {
        document.getElementById('platform-architecture').hidden=selectedPlatform==='windows';
        document.getElementById('platform-description').textContent=selectedPlatform==='linux'
          ? `Linux ${selectedArch} · 解压 ZIP 后运行 · Ubuntu 24.04 构建目标` : platforms[selectedPlatform].description;
        for(const button of document.querySelectorAll('.arch-button')) {
          const arm=button.dataset.arch==='arm64';
          button.textContent=selectedPlatform==='mac'?(arm?'Apple Silicon · M 系列':'Intel'):(arm?'ARM64':'x86_64');
          button.setAttribute('aria-pressed',String(button.dataset.arch===selectedArch));
        }
      }
      function renderSelectedRelease() {
        const config=platforms[selectedPlatform];
        const result=mostRecentFor(selectedPlatform,selectedArch);
        const title=document.getElementById('selected-release-title');
        const meta=document.getElementById('selected-release-meta');
        const button=document.getElementById('selected-download');
        const checksum=document.getElementById('checksum-link');
        const notes=document.getElementById('release-notes-link');
        const status=document.getElementById('download-status');
        status.classList.remove('error');
        if (!result) {
          title.textContent='暂未发布此平台的安装文件';
          meta.textContent='可以查看历史版本，或跟进自动构建状态。';
          setLink(button,null,'下载','暂无可下载版本');
          checksum.hidden=true;
          notes.href=repository+'/actions/workflows/package.yml'; notes.textContent='查看平台构建 ↗';
          return;
        }
        const {release,file}=result;
        title.textContent=`${release.tag_name || release.name || '发行版'} · ${file.name}`;
        meta.textContent=`${release.prerelease?'预发布版本':'正式版本'} · ${dateText(release.published_at || release.created_at)}`;
        const label=selectedPlatform==='mac' ? `下载 ${selectedArch==='arm64'?'Apple Silicon':'Intel'} 版` : `下载 ${config.title}${selectedPlatform==='linux'?' '+selectedArch:''} 版`;
        setLink(button,file.browser_download_url,label+' ↓','暂无可下载版本');
        const checksumName=config.checksums?config.checksums[selectedArch]:config.checksum;
        const sum=release.assets.find(item=>item.name===checksumName);
        checksum.hidden=!sum;
        if(sum){checksum.href=sum.browser_download_url;checksum.textContent='SHA-256 校验文件 ↗';}
        notes.href='#history-title'; notes.textContent='查看本地发行说明 ↓';
      }
      function historyFileLabel(name) {
        if(name==='Buggcraft-windows-x64.zip' || name==='Buggcraft.exe' || /windows.*\.(exe|zip)$/i.test(name)) return 'Windows';
        if(name===archNames.arm64) return 'macOS · Apple Silicon';
        if(name===archNames.x86_64) return 'macOS · Intel';
        if(name==='Buggcraft-linux-arm64.zip' || name==='Buggcraft-linux-arm64') return 'Linux · ARM64';
        if(name==='Buggcraft-linux-x86_64.zip' || name==='Buggcraft' || /^Buggcraft-linux/i.test(name)) return 'Linux · x86_64';
        return null;
      }
      function renderHistory() {
        const list=document.getElementById('release-history');
        list.replaceChildren();
        const entries=releases.slice(0,shownHistory);
        if(!entries.length){const empty=document.createElement('div');empty.className='history-empty';empty.textContent='目前还没有可公开下载的发行版本。';list.append(empty);return;}
        for(const [index,release] of entries.entries()){
          const details=document.createElement('details');details.className='history-entry';details.id=`release-${index}`;
          const summary=document.createElement('summary');
          const tag=document.createElement('span');tag.className='history-tag';tag.textContent=release.tag_name || release.name || '未命名版本';
          const date=document.createElement('span');date.className='history-date';date.textContent=dateText(release.published_at || release.created_at);
          const kind=document.createElement('span');kind.className='release-kind';kind.textContent=release.prerelease?'测试版':'正式版';
          summary.append(tag,date,kind);
          const downloads=document.createElement('div');downloads.className='history-assets';
          const preferredNames = new Set(['windows','mac','linux'].flatMap(platform=>['arm64','x86_64'].map(arch=>platformAsset(release,platform,arch)?.name)).filter(Boolean));
          const assets=(release.assets||[]).filter(file=>preferredNames.has(file.name)).map(file=>({file,label:historyFileLabel(file.name)})).filter(item=>item.label);
          for(const {file,label} of assets){const link=document.createElement('a');link.className='history-download';link.href=file.browser_download_url;link.textContent=`${label} · ${file.name} ↓`;downloads.append(link);}
          if(!assets.length){const note=document.createElement('span');note.className='history-date';note.textContent='此版本没有当前平台的安装文件。';downloads.append(note);}
          const notes=document.createElement('details');notes.className='release-notes';
          const notesSummary=document.createElement('summary');notesSummary.textContent='发行说明';
          const notesBody=document.createElement('pre');notesBody.textContent=release.body || '该版本没有发行说明。';
          notes.append(notesSummary,notesBody);downloads.append(notes);
          details.append(summary,downloads);list.append(details);
        }
        const more=document.getElementById('history-more');more.hidden=releases.length<=shownHistory;more.textContent=`显示更多版本（剩余 ${releases.length-shownHistory} 个）`;
      }
      async function loadReleases(){
        const status=document.getElementById('download-status');
        try{
          const results=await Promise.allSettled([
            fetch(releasesUrl,{cache:'no-store',headers:{Accept:'application/json'}}).then(response=>{if(!response.ok)throw new Error(`历史版本缓存 ${response.status}`);return response.json();}),
            fetch(managedReleasesUrl,{cache:'no-store',headers:{Accept:'application/json'}}).then(response=>{if(!response.ok)throw new Error(`发布后台 ${response.status}`);return response.json();})
          ]);
          const legacy=results[0].status==='fulfilled'&&Array.isArray(results[0].value.releases)?results[0].value.releases:[];
          const managed=results[1].status==='fulfilled'&&Array.isArray(results[1].value.releases)?results[1].value.releases:[];
          if(results[1].status==='rejected')throw new Error('发布状态暂时无法读取');
          const hidden=new Set(results[1].value.hidden_versions||[]);
          if(!legacy.length&&!managed.length&&results.every(result=>result.status==='rejected'))throw new Error('版本服务暂时无法读取');
          const byVersion=new Map();
          for(const release of [...legacy,...managed]){
            if(release.draft||(release.assets||[]).length===0||hidden.has((release.tag_name||'').replace(/^v/,'')))continue;
            const key=(release.tag_name||release.name||'').replace(/^v/, '').toLowerCase();
            if(key)byVersion.set(key,release);
          }
          releases=[...byVersion.values()].sort((a,b)=>Date.parse(b.published_at||b.created_at)-Date.parse(a.published_at||a.created_at));
          const current=releases.find(item=>!item.prerelease)||releases[0];
          document.getElementById('release-summary-version').textContent=current?.tag_name || '暂无公开版本';
          document.getElementById('release-summary-date').textContent=current?`${current.prerelease?'测试发行版':'正式发行版'} · ${dateText(current.published_at||current.created_at)}`:'发布版本后会显示在这里';
          document.querySelector('.release-summary').hidden=false;
          renderHistory();renderSelectedRelease();
          const cacheStatus=`后台发布即时同步 · 历史安装文件由官网缓存提供`;
          status.textContent=releases.some(item=>item.prerelease) && !releases.some(item=>!item.prerelease)
            ? `目前公开的是测试发行版。${cacheStatus}` : cacheStatus;
        }catch(error){
          document.getElementById('release-summary-version').textContent='暂时无法读取';
          document.getElementById('release-summary-date').textContent='官网版本缓存暂不可用';
          document.querySelector('.release-summary').hidden=false;
          document.getElementById('release-history').innerHTML='<div class="history-empty">官网版本缓存暂时无法读取，请稍后重试。</div>';
          status.textContent='版本列表和安装文件由官网服务器提供，暂时无法读取。';status.classList.add('error');
          renderSelectedRelease();
        }
      }
      document.getElementById('theme-toggle').addEventListener('click',()=>{
        root.dataset.theme=root.dataset.theme==='light'?'dark':'light';
        const light=root.dataset.theme==='light';
        document.getElementById('theme-toggle').textContent=light?'深色主题':'浅色主题';
        document.getElementById('theme-toggle').setAttribute('aria-pressed',String(light));
        try{localStorage.setItem('buggcraft.theme',root.dataset.theme);}catch(_){}
      });
      document.querySelectorAll('.platform-tab').forEach(button=>button.addEventListener('click',()=>selectPlatform(button.dataset.platform)));
      document.querySelectorAll('.arch-button').forEach(button=>button.addEventListener('click',()=>selectArch(button.dataset.arch)));
      document.getElementById('history-more').addEventListener('click',()=>{shownHistory+=8;renderHistory();});
      selectPlatform(selectedPlatform,false);
      selectArch(selectedArch,false);
      loadReleases();
    })();
