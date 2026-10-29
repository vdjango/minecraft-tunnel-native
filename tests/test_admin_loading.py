"""Settings must not wait for remote presence or enumerate user backups."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
from tunnel.portal import Portal


class AdminLoadingTests(unittest.IsolatedAsyncioTestCase):
    def portal(self):
        portal = Portal.__new__(Portal)
        portal.session = Mock(return_value={'csrf': 'fixture'})
        portal.state = {}
        portal.federation = SimpleNamespace(
            id='node', name='Fixture', region='', health=lambda: [], blocked=set(), seeds={},
            network_players=AsyncMock(), online_players=lambda: 3)
        portal.account_api = SimpleNamespace(store=SimpleNamespace(
            replication=SimpleNamespace(directory={'invalid': {}}), records={}))
        portal.relay = SimpleNamespace(capacity=SimpleNamespace(snapshot=lambda: {}), draining=False)
        portal.bandwidth = SimpleNamespace(snapshot=lambda: {})
        return portal

    async def test_settings_returns_while_remote_presence_is_stalled(self):
        portal = self.portal()
        portal.federation.network_players.side_effect = asyncio.Event().wait
        response = await asyncio.wait_for(portal.admin(SimpleNamespace(query={'section': 'settings'})), 1)
        value = json.loads(response.text)
        self.assertEqual(value['total'], 3)
        self.assertEqual(value['accounts'], [])
        portal.federation.network_players.assert_not_awaited()

    async def test_players_page_fetches_remote_presence(self):
        portal = self.portal()
        portal.federation.network_players.return_value = {'players': [{'username': 'Player'}], 'total': 3, 'partial': True}
        response = await portal.admin(SimpleNamespace(query={'section': 'players'}))
        self.assertEqual(json.loads(response.text)['players'][0]['username'], 'Player')
        portal.federation.network_players.assert_awaited_once()

    async def test_static_cache_does_not_cache_live_status(self):
        portal = self.portal()
        async def asset(request):
            return web.FileResponse(Path(__file__))
        for path in ('/vue-runtime-Fixture01.js', '/portal.css?v=0123456789ab'):
            response = await portal.guard(make_mocked_request('GET', path), asset)
            self.assertIn('immutable', response.headers['Cache-Control'])
        async def status(request):
            return web.json_response({'players': 0})
        response = await portal.guard(make_mocked_request('GET', '/api/status'), status)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
