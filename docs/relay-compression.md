# Relay Zstandard extension v1

This extension changes the tunnel endpoints only. Minecraft continues using its
ordinary TCP connection to the local game/forwarding port. Authentication,
directory, account and federation control messages never share a compression
context with game traffic.

## Negotiation and authorization

1. Directory `ping` includes `relay-zstd-v1` when enabled on a native node.
2. A host registers `tcpip-forward` address `buggcraft-zstd`, port `0`, in addition
   to its ordinary raw-game listener. The returned port is a private listener ID.
3. Room `describe` includes `relay_compression: "zstd-1"` only while that same room's
   owner has a live compression listener.
4. A supported visitor opens `direct-tcpip` to its room ID, port `25566`.
   Raw game port `25565` and modpack port `25564` keep their existing meanings.
5. The native node applies the same role, same-room, availability and stream
   limits before linking the visitor channel directly to the host's listener.
   A visitor cannot register a compression listener or access another room.

The visitor attempts raw port 25565 only when opening 25566 fails before any game
bytes are read. An accepted stream with invalid magic or corrupt data is closed;
it is never replayed through a second stream. Turning the policy off rejects new
compressed streams and stops advertising support; an existing stream retains its
negotiated format until closed.

## Independent framing in each direction

The direction starts with four ASCII bytes `BCZ1`. Each framed block begins with
an unsigned, little-endian base-128 varint, at most three bytes:

* `0`: switch this direction permanently to an unframed raw remainder until EOF.
* Otherwise `value = (payload_size << 1) | kind`.
* `kind=0`: raw payload, `payload_size` bytes.
* `kind=1`: another varint declares the original size, followed by a complete,
  independent Zstandard frame of `payload_size` bytes.

Both payload size and original size must be 1–65536 bytes. The Zstandard frame's
content size must equal the declared size and its window must be no larger than
65536 bytes. Unknown-size frames, dictionaries requiring external state, trailing
data, truncated varints/blocks and corrupt frames are rejected. Clean EOF is
accepted only at a frame boundary, or anywhere after the raw-remainder marker.
The opposite direction may continue sending after EOF.

## Encoding policy

Zstandard level 1 is attempted for blocks at least 256 bytes long. A compressed
candidate must save more than 32 bytes. After four unsuccessful attempts, the
encoder skips the next 31 blocks before resampling. When a direction has sent at
least 2048 raw bytes or 64 framed blocks without an overall byte saving, it sends
marker zero and continues raw. No timer or extra packet accumulation delays game
traffic. Statistics count each direction once, including framing and magic.

Game protocol compression/encryption may leave little further opportunity to
compress. The measured 6.13% reduction in the four-client vanilla test is an
application-layer measurement for that workload, not a guarantee about total
network billing. Larger savings require measuring a different, compressible
workload. Keep Minecraft's own compression configuration unchanged by default.

The C++ decoder validates the declared frame content size, frame boundary and
window before decompression, and supplies a bounded output buffer. Runtime
compression uses native Zstandard; no Python codec is required. The frozen
Python implementation is only an optional interoperability test reference.
