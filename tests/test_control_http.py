"""Exercise the product HTTPS owner against real TLS and connection failures."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
from tunnel.onboarding import setup_tls


class ControlHttpsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='bugg-control-https-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        context, _ = setup_tls(self.root)
        self.calls, self.connections, self.tasks, self.writers = [], 0, set(), set()
        self.drop_first = self.drop_response = False

        async def respond(request):
            body = await request.json()
            self.calls.append((request.transport, request.headers.get('Authorization'), body))
            if self.drop_response:
                request.transport.abort()
            return web.json_response(body)

        app = web.Application()
        app.router.add_post('/v1/probe', respond)
        self.server = TestServer(app)
        await self.server.start_server(ssl=context)
        self.addAsyncCleanup(self.server.close)

        async def accepted(reader, writer):
            task = asyncio.current_task()
            self.tasks.add(task)
            self.writers.add(writer)
            self.connections += 1
            upstream = None
            try:
                if self.drop_first and self.connections == 1:
                    await reader.read(1)
                    writer.transport.abort()
                    return
                remote, upstream = await asyncio.open_connection('127.0.0.1', self.server.port)
                self.writers.add(upstream)

                async def copy(source, target):
                    while block := await source.read(65536):
                        target.write(block)
                        await target.drain()
                    target.close()

                await asyncio.gather(copy(reader, upstream), copy(remote, writer))
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                for stream in (writer, upstream):
                    if stream:
                        stream.close()
                        self.writers.discard(stream)
                self.tasks.discard(task)

        self.proxy = await asyncio.start_server(accepted, '127.0.0.1', 0)
        self.addAsyncCleanup(self.close_proxy)
        self.origin = 'https://127.0.0.1:' + str(self.proxy.sockets[0].getsockname()[1])

    async def close_proxy(self):
        self.proxy.close()
        await self.proxy.wait_closed()
        for writer in tuple(self.writers):
            writer.close()
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)

    async def probe(self, ca=None):
        binary = os.environ['BUGGCRAFT_HTTP_PROBE']
        process = await asyncio.create_subprocess_exec(
            binary, self.origin, str(ca or self.root / 'cert.pem'),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.addAsyncCleanup(self.stop_process, process)
        output, errors = await asyncio.wait_for(process.communicate(), 20)
        self.assertEqual(output, b'')
        return process.returncode, errors.decode(errors='replace')

    async def stop_process(self, process):
        if process.returncode is None:
            process.kill()
            await process.wait()

    async def test_reuses_tls_connection_and_clears_previous_authorization(self):
        self.assertEqual(await self.probe(), (0, ''))
        self.assertEqual(self.connections, 1)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual([call[1] for call in self.calls], ['Bearer test-token', None])

    async def test_retries_handshake_reset_without_duplicate_http_operation(self):
        self.drop_first = True
        self.assertEqual(await self.probe(), (0, ''))
        self.assertEqual(self.connections, 2)
        self.assertEqual([call[2]['sequence'] for call in self.calls], [0, 1])

    async def test_does_not_replay_post_after_server_reads_body(self):
        self.drop_response = True
        status, error = await self.probe()
        self.assertEqual(status, 1)
        self.assertIn('curl=', error)
        self.assertIn('phase=response', error)
        self.assertEqual(len(self.calls), 1)

    async def test_does_not_retry_untrusted_certificate(self):
        other = self.root / 'untrusted'
        other.mkdir()
        setup_tls(other)
        status, error = await self.probe(other / 'cert.pem')
        self.assertEqual(status, 1)
        self.assertIn('curl=60', error)
        self.assertEqual(self.connections, 1)
        self.assertEqual(self.calls, [])
