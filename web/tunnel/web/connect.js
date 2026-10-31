'use strict';
(async () => {
  try {await api('/api/account/me');location.replace('/account/access');} catch {}
})();
