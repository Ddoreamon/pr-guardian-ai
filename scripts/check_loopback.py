"""Assert that a port is bound to loopback only.

Used by the Linux launcher CI job, where a real server is running. Parsing `/proc/net/tcp`
is the only dependency-free way to see the bind address, so the parser is a separate
function and unit-tested with synthetic content on any platform.

Exit codes: 0 loopback only, 1 wrong binding (or unreachable), 3 cannot verify here.
"""

from __future__ import annotations

import socket
import sys


def parse_listen(text: str, port: int) -> list[str]:
    """Return the local addresses (hex) listening on `port` from /proc/net/tcp content."""
    addresses = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        local, state = parts[1], parts[3]
        if state != "0A":  # LISTEN
            continue
        _addr, raw_port = local.split(":")
        if int(raw_port, 16) == port:
            addresses.append(_addr)
    return addresses


def check(port: int) -> int:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            pass
    except OSError as exc:
        print(f"[error] nothing is listening on 127.0.0.1:{port} ({exc})")
        return 1

    addresses: list[str] = []
    seen_proc = False
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table, encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            continue
        seen_proc = True
        addresses.extend(parse_listen(text, port))
    if not seen_proc:
        print("[unverified] /proc/net/tcp is not available; only the loopback "
              "connection was checked")
        return 3

    print(f"listen addresses for port {port}: {addresses}")
    if not any(address.startswith("0100007F") or address == "0100007F" for address in addresses):
        print("[error] no 127.0.0.1 listener found")
        return 1
    wildcard = [a for a in addresses if a in {"00000000", "00000000000000000000000000000000"}]
    if wildcard:
        print(f"[error] wildcard bind detected: {wildcard}")
        return 1
    print("[ok] bound to 127.0.0.1 only")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].isdigit():
        print("usage: check_loopback.py <port>")
        return 3
    return check(int(argv[1]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
