"""Run real browser/account flows locally; validate the C++ transport on Ubuntu.

The local UI fixture uses the original real SSH relay so Windows application
control does not prevent exercising the HTML/JS. Authentication and HTTP handlers
are the same source as production. No account or feedback endpoints are mocked.
"""
from pathlib import Path
import asyncio
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'compatibility'),str(ROOT/'web')]
from tunnel.relay import RelayServer
from tests import test_registration_browser as fixtures


class BrowserRelay(RelayServer):
    def __init__(self,*args,**kwargs):
        for key in ('workers','buffer_kib','compression'):kwargs.pop(key,None)
        super().__init__(*args,**kwargs)

    async def wait_failed(self):
        await asyncio.Future()


import tunnel.native_relay
tunnel.native_relay.RelayServer=BrowserRelay
if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromNames(['tests.'+name for name in
        (sys.argv[1:] or ['test_support_browser','test_registration_browser'])])
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
