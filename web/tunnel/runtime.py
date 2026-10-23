"""Paths and diagnostics shared by source and portable distributions."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import platform
import sys


def frozen():
    return bool(getattr(sys, 'frozen', False))


def default_directory():
    if frozen():
        return Path(sys.executable).resolve().parent / 'data'
    return Path('config/node')


def protocol_path():
    if frozen():
        return Path(sys._MEIPASS) / 'PROTOCOL_STANDARD.md'
    return Path(__file__).resolve().parents[2] / 'PROTOCOL_STANDARD.md'


def configure_logging(directory):
    log_directory = Path(directory).resolve() / 'logs'
    log_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    handler = RotatingFileHandler(log_directory / 'node.log', maxBytes=5 * 1024 * 1024,
                                  backupCount=3, encoding='utf-8', delay=True)
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s',
                        handlers=[logging.StreamHandler(), handler], force=True)
    logging.getLogger('asyncssh').setLevel(logging.WARNING)
    logging.info('Node starting: OS=%s; architecture=%s; Python=%s; frozen=%s; data=%s',
                 platform.platform(), platform.machine(), platform.python_version(),
                 frozen(), Path(directory).resolve())
    return log_directory / 'node.log'
