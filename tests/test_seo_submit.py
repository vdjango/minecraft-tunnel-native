import importlib.util
import io
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

spec=importlib.util.spec_from_file_location('seo_submit',Path(__file__).parents[1]/'scripts/seo_submit.py')
seo=importlib.util.module_from_spec(spec)
spec.loader.exec_module(seo)


class SeoSubmitTests(unittest.TestCase):
    def test_only_public_html_candidates(self):
        origin='https://mirror.bugg.icu'
        for path in ('/','/docs','/docs/help/launcher-windows','/docs/help/launcher-macos','/developers','/projects/modrinth/Test01'):
            self.assertTrue(seo.public_url(origin+path,origin))
        for path in ('/admin','/login','/v1/objects/a.jar','/docs?token=private','/account','/room-cache/x'):
            self.assertFalse(seo.public_url(origin+path,origin))
        self.assertFalse(seo.public_url('https://evil.test/',origin))

    def test_noindex_or_redirect_is_never_submitted(self):
        url='https://bugg.icu/'
        body=b'<title>Example</title><meta name="description" content="Example"><meta name="robots" content="noindex"><link rel="canonical" href="https://bugg.icu/"><main><h1>Example</h1>'+b'content '*40+b'</main>'
        with patch.object(seo,'fetch',return_value=(200,url,body)):
            self.assertIsNone(seo.page_fingerprint(url)[1])

    def test_success_deduplicates_but_failed_provider_retries(self):
        origin='https://bugg.icu'
        config={'sites':[{'origin':origin}]}
        state={}
        def submit(provider,site,urls):
            if provider=='google':raise TimeoutError()
            return {'status':'accepted','urls':len(urls)}
        with patch.object(seo,'sitemap_urls',return_value=[origin+'/']),patch.object(seo,'page_fingerprint',return_value=(origin+'/','hash','')),patch.object(seo,'submit',side_effect=submit):
            first=seo.run(config,state)
            second=seo.run(config,state)
        self.assertEqual(first['sites'][0]['providers']['indexnow']['status'],'accepted')
        self.assertEqual(second['sites'][0]['providers']['indexnow']['status'],'unchanged')
        self.assertEqual(second['sites'][0]['providers']['google']['status'],'retry_scheduled')

    def test_baidu_daily_quota_does_not_acknowledge_pending_urls(self):
        origin='https://bugg.icu'
        site={'origin':origin,'baidu':{'submit_url':'http://data.zz.baidu.com/urls?site=https://bugg.icu&token=test'}}
        error=HTTPError(site['baidu']['submit_url'],400,'Bad Request',{},io.BytesIO(b'{"error":400,"message":"over quota"}'))
        with patch.object(seo,'fetch',side_effect=error):
            result=seo.submit('baidu',site,[origin+'/'])
        self.assertEqual(result['status'],'quota_exhausted')
        state={}
        with patch.object(seo,'sitemap_urls',return_value=[origin+'/']),patch.object(seo,'page_fingerprint',return_value=(origin+'/','hash','')),patch.object(seo,'submit',return_value=result):
            seo.run({'sites':[site]},state)
        previous=state[origin+'|baidu|'+seo.provider_key(site,'baidu')]
        self.assertEqual(previous['acknowledged'],{})
        self.assertGreater(previous['retry_after'],time.time())


if __name__=='__main__':unittest.main()
