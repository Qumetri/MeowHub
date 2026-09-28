"""Generate the AmneziaWG 3.1 *client-side* obfuscation block.

Single source of truth for both generators: `awg-client.sh` shells out to it,
`web/server.py` imports it. Keep it that way — two templates drifting apart is how
you end up with configs that behave differently depending on which tool made them.

Why this can be added without touching the server
-------------------------------------------------
The params emitted here are all documented `client-side` in the amneziawg-go README,
meaning they do NOT have to match between the two ends. Verified empirically on
2026-09-28: a client carrying I1/I2, ContentPaddingAddition and all five timing ranges
handshook against our awg0 (which has none of them) in <8s and passed traffic.

The opposite class — `HeaderProtectionKey`, and any change to S1-S4 / H1-H4 — IS
server-side. Measured in an isolated pair the same day: server with HeaderProtectionKey
vs a client without it = handshake never completes, 100% loss. Do not put those here.

Per-client randomisation
------------------------
Every value below is drawn fresh per client. This is the point: with one shared set,
all peers emit one statistically identical flow and a signature burned on one user
describes all of them. Client-side params are the only ones that CAN differ per user —
S1-S4/H1-H4 are forced uniform by the protocol.
"""
import random

# A real Chrome QUIC Initial to google.com / cloudflare-quic.com, captured on the host
# 2026-09-28, is the template below. Observed across three samples:
#   datagram      exactly 1200 bytes
#   byte 0        0xce / 0xcf / 0xc0  (long header + fixed bit + Initial; the low nibble
#                                      is header-protected, so it varies per packet)
#   version       00000001            (QUIC v1)
#   DCID len      8 or 16             SCID len 20
#   token len     0
#   Length        varint, then AEAD-encrypted payload (indistinguishable from random)
# We reproduce that layout and let <r N> supply the parts that are random on the wire
# anyway: the connection IDs and the ciphertext. Length is computed so it is consistent
# with the real datagram size — a DPI that actually parses QUIC will find it well-formed.
QUIC_DATAGRAM = 1200


def quic_initial(rng):
    """One QUIC v1 client Initial, as a CPS tag string, exactly QUIC_DATAGRAM bytes."""
    b0 = rng.randrange(0xC0, 0xD0)          # Initial long header, protected low nibble
    dcid = rng.choice((8, 16))
    scid = rng.choice((0, 8, 20))
    # b0(1) + version(4) + dcidlen(1) + dcid + scidlen(1) + scid + tokenlen(1) + len(2)
    payload = QUIC_DATAGRAM - (8 + dcid + scid) - 2
    lenv = 0x4000 | payload                 # 2-byte QUIC varint
    parts = [f"<b 0x{b0:02x}00000001{dcid:02x}>", f"<r {dcid}>", f"<b 0x{scid:02x}>"]
    if scid:
        parts.append(f"<r {scid}>")
    parts.append(f"<b 0x00{lenv:04x}>")     # empty token, then Length
    parts.append(f"<r {payload}>")
    return "".join(parts)


def _rng(a, b, span_lo, span_hi, rng):
    lo = rng.randint(a, b)
    return f"{lo}-{lo + rng.randint(span_lo, span_hi)}"


def block(seed=None):
    """-> (interface_lines, peer_keepalive_value)."""
    rng = random.Random(seed)
    lines = [
        # Jc=0 on purpose. Junk packets are small random UDP blobs; sending them
        # alongside a QUIC-shaped I1 produces "QUIC Initial + 8 random blobs", which is
        # less coherent than either alone. The CPS packets ARE the pre-handshake cover.
        "Jc   = 0",
        # A real client that gets no reply retransmits its Initial, so two is natural.
        f"I1 = {quic_initial(rng)}",
        f"I2 = {quic_initial(rng)}",
        # Breaks up the fixed payload sizes the 3.1 release was written against.
        f"ContentPaddingAddition = {_rng(8, 24, 16, 40, rng)}",
        # Timing jitter: defeats flow analysis keyed on WireGuard's fixed rekey cadence
        # (REKEY_AFTER 120s / REJECT_AFTER 180s / REKEY_TIMEOUT 5s / KEEPALIVE 10s).
        # Ordering invariant: RekeyAfterTime < RejectAfterTime, always.
        f"RekeyAfterTime       = {_rng(100, 125, 5, 20, rng)}",
        f"RekeyTimeout         = {_rng(4, 6, 1, 3, rng)}",
        f"RejectAfterTime      = {_rng(165, 200, 20, 60, rng)}",
        f"KeepaliveTimeout     = {_rng(8, 11, 1, 3, rng)}",
        f"MaxHandshakeAttempts = {_rng(14, 20, 4, 10, rng)}",
    ]
    return "\n".join(lines), _rng(18, 25, 4, 10, rng)


if __name__ == "__main__":
    iface, keepalive = block()
    print(iface)
    print(f"__KEEPALIVE__={keepalive}")


def qr_payload(text):
    """Strip a client config down to what has to survive a camera scan.

    Why: the 3.1 block roughly doubled config size (706 -> 1222 bytes), which pushed the
    QR from 97 modules to 125. That is a much denser code, and phone cameras scanning a
    laptop screen started failing on it — a partial decode leaves the config starting
    mid-file, so the app reports "unknown section" rather than a scan error, which is a
    confusing way to learn your QR is too big.

    Comments and the alignment padding are ~45% of the file and carry nothing the parser
    needs, so the QR gets a minified copy while the downloadable .conf stays readable.
    670 bytes -> 97 modules, exactly the density that already scans reliably here.
    """
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()   # same rule both app parsers use
        if not line:
            continue
        if "=" in line and not line.startswith("["):
            k, v = line.split("=", 1)          # only the first =; I1 values contain none
            line = f"{k.strip()}={v.strip()}"
        out.append(line)
    return "\n".join(out) + "\n"
