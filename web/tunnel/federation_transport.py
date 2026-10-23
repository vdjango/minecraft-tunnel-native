"""Small pool of pinned SSH connections; no mesh of idle outbound sockets."""
import asyncio
from contextlib import asynccontextmanager
import time

import asyncssh

from .federation_schedule import FANOUT


class Transport:
    def __init__(self, key, node_id):
        self.key, self.node_id = key, node_id
        self.connections = {}
        self.locks = {}
        self.active = set()
        self.connecting = set()
        self.slots = asyncio.Semaphore(FANOUT)

    @asynccontextmanager
    async def connection(self, profile, node_id):
        async with self.slots, self.locks.setdefault(node_id, asyncio.Lock()):
            address = (profile['server_host'], profile['server_port'], profile['server_public_key'])
            entry = self.connections.get(node_id)
            if entry and (entry[1] != address or entry[0].is_closed()):
                self.connections.pop(node_id)[0].close()
                entry = None
            if not entry:
                if len(self.connections) + len(self.connecting) >= FANOUT:
                    oldest = min((n for n in self.connections if n not in self.active),
                                 key=lambda n: self.connections[n][2])
                    self.connections.pop(oldest)[0].close()
                self.connecting.add(node_id)
                try:
                    conn = await asyncssh.connect(address[0], address[1],
                        username='federation:' + self.node_id,
                        known_hosts=([asyncssh.import_public_key(address[2])], [], []),
                        client_keys=[self.key], agent_path=None, preferred_auth=['publickey'],
                        connect_timeout=2, login_timeout=3, encoding=None, config=None,
                        keepalive_interval=5, keepalive_count_max=3,
                        x509_trusted_certs=None, x509_trusted_cert_paths=[])
                finally:
                    self.connecting.discard(node_id)
            else:
                conn = entry[0]
            self.connections[node_id] = (conn, address, time.monotonic())
            self.active.add(node_id)
            try:
                yield conn
            except BaseException:
                self.connections.pop(node_id, None)
                conn.close()
                raise
            finally:
                self.active.discard(node_id)

    async def close(self):
        connections = [entry[0] for entry in self.connections.values()]
        self.connections.clear()
        for conn in connections:
            conn.close()
        await asyncio.gather(*(conn.wait_closed() for conn in connections), return_exceptions=True)
