"""Headless node configuration and role-specific deployment payloads."""
import configparser
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from service_package import copy_service, identity_assets
from tests.service_fixture import ServiceFixture
from tunnel.provisioning import initialize


class ServicePackageTests(ServiceFixture, unittest.IsolatedAsyncioTestCase):
    async def test_managed_configuration_has_no_admin_credentials_or_account_state(self):
        target=self.directory/'new-node'
        initialize(target,'127.0.0.1',4444,control=dict(self.settings,
            ca_file=str(self.directory/'web-cert.pem'),credential_file=str(self.directory/'admin-token'),
            endpoint='127.0.0.1:4444',proof_endpoint='127.0.0.1:18443'))
        config=configparser.ConfigParser(interpolation=None)
        config.read(target/'server.conf')
        self.assertEqual(config['control']['mode'],'native')
        self.assertEqual(config['control']['origin'],self.settings['origin'])
        self.assertEqual(config['relay']['compression'],'off')
        self.assertFalse((target/'admin-credentials.txt').exists())
        self.assertFalse((target/'relay-profile.json').exists())
        self.assertNotIn('admin_hash',(target/'node-state.json').read_text())
        with self.assertRaises(ValueError):
            initialize(target,'127.0.0.1',4444,control=self.settings)

    async def test_role_packages_exclude_unused_frontends(self):
        source=Path(__file__).resolve().parents[1]/'web'
        relay,identity=self.directory/'relay-package',self.directory/'identity-package'
        copy_service(source,relay,role='relay')
        copy_service(source,identity,role='identity')
        self.assertFalse((relay/'tunnel/web').exists())
        assets=identity_assets(source/'tunnel/web')
        self.assertIn('account.html',assets)
        self.assertNotIn('portal.css',assets)
        self.assertNotIn('theme.css',assets)
        self.assertTrue(any(n.startswith('vue-page-account-') and n.endswith('.js') for n in assets))
        self.assertTrue(any(n.startswith('vue-') and n.endswith('.css') for n in assets))
        self.assertFalse(any('admin' in n or 'setup' in n or 'feedback' in n or 'network' in n for n in assets))
        self.assertEqual({p.name for p in (identity/'tunnel/web').iterdir()},set(assets))
