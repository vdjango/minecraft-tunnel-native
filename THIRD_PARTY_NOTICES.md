# Third party components

The project retains the original AGPL-3.0-or-later license in `LICENSE`.

| Component | Version | License | Source |
| --- | --- | --- | --- |
| libssh | 0.12.2 | LGPL-2.1-or-later | https://www.libssh.org/ |
| nlohmann/json | 3.12.0 | MIT | https://github.com/nlohmann/json |
| OpenSSL | 3.x, supplied by build environment | Apache-2.0 | https://www.openssl.org/ |
| libcurl | supplied by build environment | curl license | https://curl.se/docs/copyright.html |
| Python management dependencies | pinned in requirements.txt | See installed package metadata | Original Python management plane |

libssh is dynamically linked. Its pinned source, including `COPYING`, is fetched
by `scripts/fetch_deps.py`; retain these sources and license files when distributing
a binary package. The JSON MIT notice is in `licenses/nlohmann-json.txt`. OpenSSL and
compiler runtime notices belong with the corresponding runtime libraries.
The libcurl license text is in `licenses/curl.txt`.

The optional client compression dependency is python-zstandard 0.25.0, using its
bundled Zstandard implementation. It is a launcher dependency, not a node runtime
dependency: https://github.com/indygreg/python-zstandard (BSD license) and
https://github.com/facebook/zstd (BSD/GPL dual license).
