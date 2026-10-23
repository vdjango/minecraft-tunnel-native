"""Run the public ctypes binding in an isolated process for transport tests."""
import importlib.util
from pathlib import Path
import sys


if __name__ == '__main__':
    path = Path(__file__).resolve().parents[1] / 'bindings/python/buggcraft_bridge.py'
    spec = importlib.util.spec_from_file_location('buggcraft_bridge', path)
    binding = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(binding)
    with binding.Bridge(sys.argv[1]) as bridge:
        for request in sys.stdin.buffer:
            response = bridge.exchange(request.rstrip(b'\r\n'))
            sys.stdout.buffer.write(response.rstrip(b'\n') + b'\n')
            sys.stdout.buffer.flush()
