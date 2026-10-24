"""Small C ABI binding. All multiplayer work runs inside the native library."""
import ctypes
import json
import os
from pathlib import Path
import threading


class Bridge:
    def __init__(self, path):
        path = Path(path).resolve()
        self._directory = os.add_dll_directory(str(path.parent)) if os.name == 'nt' else None
        self._lock = threading.Lock()
        self._handle = ctypes.c_void_p()
        try:
            # Only this absolute DLL's directory and System32 participate in
            # dependency resolution. Never search cwd, PATH or Qt directories.
            options = {'winmode': 0x00000100 | 0x00000400 | 0x00000800} if os.name == 'nt' else {}
            self._lib = ctypes.CDLL(str(path), **options)
            lib = self._lib
            lib.buggcraft_bridge_abi_version.argtypes = []
            lib.buggcraft_bridge_abi_version.restype = ctypes.c_uint32
            lib.buggcraft_bridge_create.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
            lib.buggcraft_bridge_create.restype = ctypes.c_int32
            lib.buggcraft_bridge_call.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
                                                ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t)]
            lib.buggcraft_bridge_call.restype = ctypes.c_int32
            lib.buggcraft_bridge_free.argtypes = [ctypes.c_void_p]
            lib.buggcraft_bridge_free.restype = None
            lib.buggcraft_bridge_destroy.argtypes = [ctypes.c_void_p]
            lib.buggcraft_bridge_destroy.restype = None
            if lib.buggcraft_bridge_abi_version() != 1:
                raise RuntimeError('Unsupported native bridge ABI')
            if lib.buggcraft_bridge_create(ctypes.byref(self._handle)):
                raise RuntimeError('Native bridge initialization failed')
        except BaseException:
            if self._directory is not None:
                self._directory.close()
            raise

    def exchange(self, payload):
        if not isinstance(payload, bytes) or len(payload) > 8192:
            raise ValueError('Expected at most 8192 UTF-8 JSON request bytes')
        with self._lock:
            if not self._handle:
                raise RuntimeError('Native bridge is closed')
            output, size = ctypes.c_void_p(), ctypes.c_size_t()
            status = self._lib.buggcraft_bridge_call(self._handle, payload, len(payload),
                ctypes.byref(output), ctypes.byref(size))
            try:
                if status:
                    raise RuntimeError('Native bridge call failed: ' + str(status))
                return ctypes.string_at(output, size.value) if output else b''
            finally:
                self._lib.buggcraft_bridge_free(output)

    def request(self, method, params=None, request_id=1):
        return json.loads(self.exchange(json.dumps(dict(jsonrpc='2.0', id=request_id,
            method=method, params=params or {}), separators=(',', ':')).encode('utf-8')))

    def close(self):
        with self._lock:
            if self._handle:
                self._lib.buggcraft_bridge_destroy(self._handle)
                self._handle = ctypes.c_void_p()
            if self._directory is not None:
                self._directory.close()
                self._directory = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
