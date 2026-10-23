"""Exercise the shared C ABI, relocated installs and real multiplayer sessions."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests import test_bridge as fixture
from tests import test_bridge_services as services

ROOT = Path(__file__).resolve().parents[1]
NAME = 'buggcraft_bridge.dll' if os.name == 'nt' else (
    'libbuggcraft_bridge.dylib' if sys.platform == 'darwin' else 'libbuggcraft_bridge.so')
LIBRARY = Path(os.environ.get('BUGGCRAFT_BRIDGE_LIBRARY', str(ROOT / 'build' / NAME))).resolve()
spec = importlib.util.spec_from_file_location('native_ffi_test', ROOT / 'bindings/python/buggcraft_bridge.py')
binding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(binding)


@unittest.skipUnless(LIBRARY.is_file(), 'Build the native shared library first')
class LibraryAbiTests(unittest.TestCase):
    def test_independent_handles_concurrent_calls_and_repeated_cleanup(self):
        with binding.Bridge(LIBRARY) as first, binding.Bridge(LIBRARY) as second:
            def request(index):
                bridge = first if index % 2 else second
                response = bridge.request('bridge.initialize', request_id=index)
                self.assertEqual(response['id'], index)
                self.assertEqual(response['result']['build']['implementation'], 'cpp')
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(request, range(24)))
            first.close()
            first.close()
            self.assertIn('result', second.request('bridge.initialize'))
            with self.assertRaises(RuntimeError):
                first.exchange(b'{}')
        with binding.Bridge(LIBRARY) as recreated:
            self.assertEqual(recreated.request('bridge.initialize')['result']['build']['implementation'], 'cpp')

    def test_relocated_unicode_install_ignores_working_directory_and_path(self):
        package = Path(os.environ.get('BUGGCRAFT_BRIDGE_PACKAGE', str(ROOT / 'runtime'))).resolve()
        source = next((package / folder / NAME for folder in ('bin', 'lib')
                       if (package / folder / NAME).is_file()), None)
        if source is None:
            self.skipTest('Set BUGGCRAFT_BRIDGE_PACKAGE to a complete CMake install')
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / '中文 空格' / 'native'
            shutil.copytree(package, target)
            library = target / source.relative_to(package)
            ffi = target / 'share/buggcraft-native/python/buggcraft_bridge.py'
            unrelated = Path(temporary) / 'unrelated cwd'
            unrelated.mkdir()
            # An invalid same-named DLL in cwd must never win dependency lookup.
            (unrelated / NAME).write_bytes(b'not a shared library')
            script = '''import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('ffi', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with module.Bridge(sys.argv[2]) as bridge:
    print(json.dumps(bridge.request('bridge.initialize')))
'''
            result = subprocess.run([sys.executable, '-I', '-c', script, str(ffi), str(library)],
                cwd=unrelated, env=dict(os.environ, PATH=str(unrelated)),
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['result']['build']['implementation'], 'cpp')


@unittest.skipUnless(LIBRARY.is_file(), 'Build the native shared library first')
class LibraryServicesTests(services.BridgeServicesTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        environment = patch.dict(os.environ, BUGGCRAFT_BRIDGE_LOG_DIR=str(self.logs / 'shared-library'))
        environment.start()
        self.addCleanup(environment.stop)

    async def process(self):
        self.process_count += 1
        bridge = await asyncio.to_thread(binding.Bridge, LIBRARY)
        self.addAsyncCleanup(asyncio.to_thread, bridge.close)
        return bridge

    async def call(self, process, method, params=None):
        if isinstance(process, asyncio.subprocess.Process):
            return await fixture.BridgeTests.call(self, process, method, params)
        response = await asyncio.to_thread(process.request, method, params)
        self.assertNotIn('error', response, response)
        return response['result']

    async def test_library_p2p_and_relay_share_the_same_native_session(self):
        await fixture.BridgeTests.test_two_native_processes_mixed_p2p_relay(self)

    async def test_library_host_with_executable_guest(self):
        await self.mixed_pair(True)

    async def test_executable_host_with_library_guest(self):
        await self.mixed_pair(False)

    async def mixed_pair(self, library_host):
        library = await self.process()
        executable = await fixture.BridgeTests.process(self)
        host, guest = (library, executable) if library_host else (executable, library)
        hn, gn = await self.add(host), await self.add(guest)
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=self.port, transport='relay'))
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=hosted['invite'], transport='relay'))
        await asyncio.gather(self.exchange(joined), self.exchange(joined, b'compressible terrain' * 100000))
        await self.call(guest, 'room.close', dict(session=joined['session']))
        await self.call(host, 'room.close', dict(session=hosted['session']))


if __name__ == '__main__':
    unittest.main()
