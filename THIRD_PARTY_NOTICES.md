# Third party components

The project retains the original AGPL-3.0-or-later license in `LICENSE`.

| Component | Version | License | Source |
| --- | --- | --- | --- |
| libssh | 0.12.2 | LGPL-2.1-or-later | https://www.libssh.org/ |
| nlohmann/json | 3.12.0 | MIT | https://github.com/nlohmann/json |
| OpenSSL | 3.x, supplied by build environment | Apache-2.0 | https://www.openssl.org/ |
| libcurl | supplied by build environment | curl license | https://curl.se/docs/copyright.html |
| aklomp/base64 | 0.5.2 | BSD-2-Clause | https://github.com/aklomp/base64/tree/v0.5.2 |
| Python management dependencies (legacy service only, not the native bridge) | pinned in requirements.txt | See installed package metadata | Original Python management plane |

libssh is dynamically linked by default; the optional standalone bridge links it
statically and embeds notices accessible with `--licenses`. Distributions of that
profile must also provide corresponding source and relinkable application objects
separately, so users can replace the LGPL library without requiring an installer.
Its pinned source, including `COPYING`, is fetched
by `scripts/fetch_deps.py`; retain these sources and license files when distributing
a binary package. The JSON MIT notice is in `licenses/nlohmann-json.txt`. OpenSSL and
compiler runtime notices belong with the corresponding runtime libraries.
The libcurl license text is in `licenses/curl.txt`.
The pinned Base64 source is fetched by `scripts/fetch_deps.py`; its LICENSE is
installed as `aklomp-base64-BSD-2-Clause.txt`. It is statically linked with hidden
symbols and isolated ISA flags. Our adapter uses its immutable codec function
table and per-call stream state instead of its mutable global dispatcher.

The native bridge links Zstandard directly; see `licenses/zstd-BSD-3-Clause.txt`.
Legacy Python compatibility clients may use python-zstandard 0.25.0, using its
bundled Zstandard implementation. It is not a native bridge runtime dependency:
https://github.com/indygreg/python-zstandard (BSD license) and
https://github.com/facebook/zstd (BSD/GPL dual license).
