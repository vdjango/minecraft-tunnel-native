"""Hold TCP bytes without FIN/RST: the kernels still ACK both connections."""
import asyncio
import errno


class BlackholeProxy:
    def __init__(self, port):
        self.destination = port
        self.allowed = [asyncio.Event(), asyncio.Event()]
        for event in self.allowed:
            event.set()
        self.tasks = set()
        self.writers = set()

    async def start(self):
        self.server = await asyncio.start_server(self.accept, '127.0.0.1', 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    def mute(self, direction=None):
        for i in range(2) if direction is None else [direction]:
            self.allowed[i].clear()

    def resume(self):
        for event in self.allowed:
            event.set()

    async def accept(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        remote = None
        async def forward(source, destination, gate):
            while data := await source.read(65536):
                await gate.wait()
                destination.write(data)
                await destination.drain()
            destination.write_eof()
        try:
            other, remote = await asyncio.open_connection('127.0.0.1', self.destination)
            self.writers.add(remote)
            async with asyncio.TaskGroup() as group:
                group.create_task(forward(reader, remote, self.allowed[0]))
                group.create_task(forward(other, writer, self.allowed[1]))
        except (OSError, ExceptionGroup):
            pass
        finally:
            writers = [writer] + ([remote] if remote else [])
            for stream in writers:
                stream.transport.abort()
            try:
                async with asyncio.timeout(2):
                    await asyncio.gather(*(stream.wait_closed() for stream in writers), return_exceptions=True)
            finally:
                self.writers.difference_update(writers)
            self.tasks.discard(task)

    async def close(self):
        self.server.close()
        tasks = list(self.tasks)
        for writer in list(self.writers):
            writer.transport.abort()
        for task in tasks:
            task.cancel()
        async with asyncio.timeout(3):
            await asyncio.gather(*tasks, return_exceptions=True)
            # Python 3.12 waits for accepted connections too. Release them
            # before waiting for the listening server to finish closing.
            await self.server.wait_closed()


class TcpOnlyRelay(BlackholeProxy):
    async def start(self, host, udp_factory=None):
        if udp_factory is None:
            self.server = await asyncio.start_server(self.accept, host, 0)
            self.port = self.server.sockets[0].getsockname()[1]
            return self
        # Windows may exclude a port for UDP even when TCP can bind it. Let
        # UDP choose its own usable port, then reserve that same port for TCP.
        loop = asyncio.get_running_loop()
        for attempt in range(16):
            transport, protocol = await loop.create_datagram_endpoint(
                udp_factory, local_addr=(host, 0))
            port = transport.get_extra_info('sockname')[1]
            try:
                server = await asyncio.start_server(self.accept, host, port)
            except OSError as error:
                transport.close()
                await asyncio.sleep(0)
                if (error.errno not in (errno.EACCES, errno.EADDRINUSE)
                        and getattr(error, 'winerror', None) not in (10013, 10048)):
                    raise
                if attempt == 15:
                    raise
            else:
                self.server, self.port = server, port
                self.udp_transport, self.udp_protocol = transport, protocol
                return self

    async def close(self):
        transport = getattr(self, 'udp_transport', None)
        if transport is not None:
            transport.close()
        await super().close()
