"""Production entry point for a native Buggcraft relay node.

Run with ``python src/main.py``; first setup happens in the browser.
"""
import argparse
import asyncio
import configparser
import json
import logging
import os
from pathlib import Path
import signal
import ssl
import sys

from tunnel.runtime import configure_logging, default_directory, frozen

async def run(config_path):
    import asyncssh
    from tunnel.native_relay import RelayServer

    config = configparser.ConfigParser(interpolation=None)
    if not config.read(config_path, encoding="utf-8"):
        raise SystemExit("Relay configuration file missing")
    settings = config["relay"]
    base = Path(config_path).resolve().parent
    token = (base / settings["token_file"]).read_text(encoding="utf-8").strip()
    relay = RelayServer(
        settings.get("host", "0.0.0.0"), settings.getint("port", 3333),
        host_key=str(base / settings["host_key"]), token=token,
        max_connections=settings.getint("max_connections", 512),
        max_rooms=settings.getint("max_rooms", 128),
        max_streams=settings.getint("max_streams_per_room", 128),
        max_total_streams=settings.getint("max_total_streams", 512),
        max_client_streams=settings.getint("max_streams_per_client", 8),
        workers=settings.getint('workers', fallback=None),
        buffer_kib=settings.getint('buffer_kib', 64),
        compression=settings.get('compression', 'auto'),
    )
    portal = federation = journal_context = None
    if config.has_section("web"):
        from tunnel.federation import Federation, identity, validate_profile
        from tunnel.portal import Portal

        web_settings = config["web"]
        state_path = base / web_settings.get("state_file", "node-state.json")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        profile = json.loads((base / web_settings.get(
            "profile_file", "relay-profile.json")).read_text(encoding="utf-8"))
        # Advertise only the configured public entrance; never infer an origin
        # address from the bind socket.
        if settings.get("entry_host"):
            profile["server_host"] = settings["entry_host"]
        if settings.get("entry_port"):
            profile["server_port"] = settings.getint("entry_port")
        federation = Federation(
            asyncssh.read_private_key(str(base / settings["host_key"])), profile,
            network_key=state["network_key"], name=state["name"],
            blocked=state.get("blocked", []),
        )
        seeds = state.get("seeds", [])
        federation.seeds = {
            identity(item["server_public_key"]): validate_profile(item) for item in seeds
        }
        federation.relay = relay
        relay.federation = federation
        portal = Portal(relay, federation, state_path)

    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    if os.name != "nt":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stopped.set)
    try:
        if not portal:
            from tunnel.diagnostics import Diagnostics
            relay.diagnostics = Diagnostics(base / 'logs' / 'players', relay)
            journal_context = relay.diagnostics.lifecycle(None)
            await anext(journal_context)
        await relay.start()
        if portal:
            tls = None
            if web_settings.get("tls_cert"):
                tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                tls.minimum_version = ssl.TLSVersion.TLSv1_2
                tls.load_cert_chain(base / web_settings["tls_cert"], base / web_settings["tls_key"])
            await portal.start(web_settings.get("host", "127.0.0.1"),
                               web_settings.getint("port", 18443), tls)
            federation.task = asyncio.create_task(federation.run())
        stop_task = asyncio.create_task(stopped.wait())
        failure_task = asyncio.create_task(relay.wait_failed())
        try:
            done, _ = await asyncio.wait((stop_task, failure_task), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            stop_task.cancel()
            failure_task.cancel()
            await asyncio.gather(stop_task, failure_task, return_exceptions=True)
    finally:
        if federation:
            await federation.close()
        # Keep the journal writer alive until relay connection_lost callbacks
        # have recorded the shutdown of every player session.
        try:
            await relay.close()
        finally:
            if portal:
                await portal.close()
            if journal_context:
                await journal_context.aclose()


def main():
    parser = argparse.ArgumentParser(description="Run one Buggcraft federation relay node")
    parser.add_argument("--install-service", action="store_true", help="Install or update the native systemd service")
    parser.add_argument("--config", help="Run an existing configuration (never initialize it)")
    parser.add_argument("--directory", default=str(default_directory()), help="Persistent node data directory")
    parser.add_argument("--web-port", type=int, default=18443, help="First setup HTTPS port")
    parser.add_argument("--setup-host", default="0.0.0.0", help="First setup bind address")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the local setup browser")
    args = parser.parse_args()
    try:
        if not 1 <= args.web_port <= 65535:
            parser.error("--web-port must be between 1 and 65535")
        if args.install_service:
            if args.config or args.directory != str(default_directory()) or args.setup_host != '0.0.0.0':
                parser.error("--install-service uses its own data directory and bind address")
            from tunnel.service import install
            try:
                install(web_port=args.web_port)
            except (OSError, RuntimeError) as error:
                raise SystemExit(str(error)) from None
            return
        directory = Path(args.config).resolve().parent if args.config else Path(args.directory)
        log_path = configure_logging(directory)
        print(f'Log file: {log_path}', flush=True)
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.exception('Node stopped unexpectedly')
        if frozen() and sys.platform == 'win32' and sys.stdin and sys.stdin.isatty():
            input('Startup failed. See data/logs/node.log. Press Enter to close...')
        raise SystemExit(1) from None


async def serve(args):
    """Use the same entry and directory for first setup and subsequent starts."""
    if args.config:
        config_path = Path(args.config)
    else:
        config_path = Path(args.directory).resolve() / 'server.conf'
        if not config_path.exists():
            from tunnel.onboarding import first_run
            options = {}
            if frozen() and sys.platform == 'win32' and not getattr(args, 'no_browser', False):
                options['open_browser'] = True
            config_path = await first_run(args.directory, host=args.setup_host,
                                         web_port=args.web_port, **options)
    await run(config_path)


if __name__ == "__main__":
    main()
