"""Real forwarding journals plus backpressure and bounded-write regressions."""
import asyncio
import json
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch
from tests import test_relay_security as fixtures
from tunnel.diagnostics import Diagnostics
from tunnel.relay import CountedForwarder


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    connect=fixtures.RelaySecurityTests.connect
    call=fixtures.RelaySecurityTests.call

    async def asyncSetUp(self):
        await fixtures.RelaySecurityTests.asyncSetUp(self)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.diag=Diagnostics(Path(self.temp.name)/'players',self.relay)
        self.relay.diagnostics=self.diag

    async def asyncTearDown(self):
        await fixtures.RelaySecurityTests.asyncTearDown(self)
        await self.diag.flush()

    async def test_real_game_bytes_eof_and_disconnect_are_journaled_without_payload_or_credentials(self):
        async def echo(reader,writer):
            try:
                while data:=await reader.read(65536):writer.write(data);await writer.drain()
                writer.write_eof();await writer.drain()
            finally:writer.close();await writer.wait_closed()
        server=await asyncio.start_server(echo,'127.0.0.1',0);self.addAsyncCleanup(server.wait_closed);self.addCleanup(server.close)
        secret=secrets.token_urlsafe(32);host=await self.connect('host',secret)
        reverse=await host.forward_remote_port('127.0.0.1',0,'127.0.0.1',server.sockets[0].getsockname()[1]);self.addCleanup(reverse.close)
        client=await self.connect('client',secret)
        from tunnel.relay import room_id
        reader,writer=await client.open_connection(room_id(secret),25565)
        payload=b'\x10\x00\xfd\x05\x09localhost\x63\xdd\x02'+b'never-save-private-game-payload-'+secrets.token_bytes(64)
        writer.write(payload);await writer.drain();self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)),3),payload)
        writer.write_eof();await asyncio.wait_for(reader.read(),3);writer.close();await writer.wait_closed()
        client.close();await client.wait_closed();await asyncio.sleep(.05);await self.diag.flush()
        files=list(self.diag.root.glob('*/*.jsonl'))
        self.assertEqual(sum('.events.' not in p.name for p in files),2)
        self.assertEqual(sum('.events.' in p.name for p in files),2)
        journal=''.join(p.read_text(encoding='utf-8') for p in files)
        for private in (secret,self.relay.token,'never-save-private-game-payload'):self.assertNotIn(private,journal)
        events=[json.loads(row) for p in files for row in p.read_text(encoding='utf-8').splitlines()]
        closed=next(e for e in events if e['event']=='stream_closed')
        self.assertEqual(closed['upload_bytes'],len(payload));self.assertEqual(closed['download_bytes'],len(payload))
        self.assertTrue(any(e['event']=='stream_eof' for e in events));self.assertTrue(any(e['event']=='disconnected' for e in events))
        fresh=Diagnostics(self.diag.root,self.relay);self.assertEqual(len(fresh.sessions()),2)

    async def test_invalid_reports_do_not_escape_identity_or_leak_other_players(self):
        directory=await self.connect();result=await self.call(directory,{'op':'diagnostics','report':{'path':'relay'}});self.assertFalse(result['ok'])
        secret=secrets.token_urlsafe(32);host=await self.connect('host',secret)
        result=await self.call(host,{'op':'diagnostics','report':{'path':'relay','game_rtt_ms':float('nan')}});self.assertFalse(result['ok'])
        result=await self.call(host,{'op':'diagnostics','report':{'path':'relay','game_rtt_ms':15.5,'token':'must-not-log'}});self.assertTrue(result['ok'])
        result=await self.call(host,{'op':'diagnostics','report':{'path':'relay'}});self.assertFalse(result['ok'])
        await self.diag.flush();journal=''.join(p.read_text(encoding='utf-8') for p in self.diag.root.glob('*/*.jsonl'));self.assertNotIn('must-not-log',journal);self.assertIn('15.5',journal)
        for player,session in [('../bad','x'),('x','../bad')]:
            with self.assertRaises(ValueError):self.diag.read(player,session)

    async def test_backpressure_lifecycle_queue_overflow_and_disk_failure_are_visible(self):
        secret=secrets.token_urlsafe(32);await self.connect('host',secret);await self.connect('client',secret)
        peer=next(p for p in self.relay.peers if p.role=='client');room=self.relay.rooms[peer.room]
        from asyncssh.forward import SSHForwarder
        counted=CountedForwarder(SSHForwarder(),room,peer)
        counted.block('download');await asyncio.sleep(.01);counted.unblock('download');counted.close();counted.close()
        ends=[row for _,_,row in self.diag.queue if row['event']=='backpressure_end']
        self.assertEqual(len(ends),1);self.assertGreaterEqual(ends[0]['duration_ms'],5)
        self.assertEqual(sum(row['event']=='stream_closed' for _,_,row in self.diag.queue),1)
        self.diag.queue=__import__('collections').deque(self.diag.queue,maxlen=5)
        for _ in range(10):self.diag.emit(peer,'test_marker')
        self.assertGreater(self.diag.dropped,0)
        with patch.object(Path,'mkdir',side_effect=OSError('Disk full')):await self.diag.flush()
        self.assertEqual(self.diag.write_errors,2)
        # A journal failure must not stop actual authenticated room operations.
        self.assertTrue((await self.call(self.connections[-1],{'op':'ping'}))['ok'])
