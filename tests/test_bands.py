import os
import socket
import time
from pathlib import Path


def children():
    """Live child processes of this test process running the fake visstream."""
    n = 0
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / "stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            state = stat.rsplit(")", 1)[1].split()[0]
            if ppid == os.getpid() and state != "Z" and b"fake_visstream" in (d / "cmdline").read_bytes():
                n += 1
        except (OSError, ValueError, IndexError):
            continue
    return n


def zombies():
    n = 0
    for d in Path("/proc").iterdir():
        try:
            stat = (d / "stat").read_text()
            if int(stat.rsplit(")", 1)[1].split()[1]) == os.getpid() and stat.rsplit(")", 1)[1].split()[0] == "Z":
                n += 1
        except (OSError, ValueError, IndexError):
            continue
    return n


def client(port):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(f"GET /bands HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
    return s


def read_until(s, marker, limit=5.0):
    buf, end = b"", time.monotonic() + limit
    while marker not in buf and time.monotonic() < end:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def wait_for(cond, limit=3.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def test_one_visstream_for_many_clients(start):
    b = start(bands_mode="emit")
    socks = [client(b.port) for _ in range(3)]
    for s in socks:
        assert b"data: {\"bands\"" in read_until(s, b"data: ")
    bands = b.svc["bands"]
    assert bands.spawned == 1 and children() == 1
    for s in socks:
        s.close()
    assert wait_for(lambda: children() == 0, 2.0)
    assert zombies() == 0


def test_silent_visstream_is_stopped_when_clients_leave(start):
    b = start(bands_mode="silent")
    socks = [client(b.port) for _ in range(3)]
    for s in socks:
        assert b"200 OK" in read_until(s, b": bands")
    assert wait_for(lambda: children() == 1)
    for s in socks:
        s.close()
    assert wait_for(lambda: children() == 0, 2.0), "visstream outlived its clients"
    assert wait_for(lambda: not b.svc["bands"].subs, 2.0)


def test_visstream_comes_back_after_it_dies(start):
    b = start(bands_mode="die")
    b.svc["bands"].retry = 0.2
    s = client(b.port)
    read_until(s, b"data: ")
    assert wait_for(lambda: b.svc["bands"].spawned >= 2, 3.0)
    s.close()
    assert wait_for(lambda: children() == 0, 2.0)
