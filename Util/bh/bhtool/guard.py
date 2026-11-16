"""Laptop guard: a dead-man switch for testing.

A robot deployed with ``bh deploy --guard`` runs the EmergencyButtonProvider
(Src/Modules/Infrastructure/EmergencyButtonProvider). It listens on UDP 9910
and treats the robot as *stopped* (it stands still, or stays down if it fell)
unless it received the heartbeat "noemergency" within the last second.

So the robot can only move while this program runs and says so:
  * ENTER toggles between RUN and STOP,
  * Ctrl+C, closing the lid, a dead battery or a lost network all mean STOP.
It is a soft stop (the robot keeps standing). For a hard stop use the red
F1 button on the robot or ``bh stop``.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import List, Sequence

from . import core
from .core import say

PORT = 9910
RELEASE = b"noemergency\x00"   # the robot compares with strcmp, so the NUL matters
STOP = b"emergency\x00"
INTERVAL = 0.2                 # robot-side timeout is 1 s: five heartbeats per timeout


class Guard:
    def __init__(self, ips: Sequence[str]):
        self.ips = list(dict.fromkeys(ip for ip in ips if ip))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.released = False
        self.running = False
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _send(self, payload: bytes) -> None:
        for ip in self.ips:
            try:
                self.sock.sendto(payload, (ip, PORT))
            except OSError:
                pass

    def _loop(self) -> None:
        while self.running:
            with self.lock:
                payload = RELEASE if self.released else STOP
            self._send(payload)
            time.sleep(INTERVAL)

    def start(self) -> None:
        self.running = True
        self.thread.start()

    def set_released(self, released: bool) -> None:
        with self.lock:
            self.released = released
        if not released:
            self._send(STOP)

    def close(self) -> None:
        """Stop the heartbeat and tell the robots explicitly to stop."""
        self.running = False
        if self.thread.is_alive():
            self.thread.join(timeout=1)
        for _ in range(5):
            self._send(STOP)
            time.sleep(0.05)
        self.sock.close()


def guard_ips(robots: Sequence[str]) -> List[str]:
    ips: List[str] = []
    for spec in robots:
        t = core.resolve_target(spec, probe=False)
        ips += [t.robot.lan, t.robot.wlan] if t.robot else [t.ip]
    return ips


def cmd_guard(args) -> int:
    specs = args.robots or [r.name for r in core.load_robots()]
    if not specs:
        raise core.BhError("no robots registered (bh robot add ...)")
    g = Guard(guard_ips(specs))
    g.start()
    say("Guard for %s  (heartbeat to %s, UDP %d)" % (", ".join(specs), ", ".join(g.ips), PORT))
    say("Robots must have been deployed with:  bh deploy <robot> --guard")
    say("ENTER = toggle RUN/STOP.   Ctrl+C = STOP and quit.   Hard stop: red button on the robot or 'bh stop'.")
    try:
        while True:
            say(">>> RUN - robots may move <<<" if g.released else "--- STOP - robots stand still ---")
            input()
            g.set_released(not g.released)
    except (KeyboardInterrupt, EOFError):
        say()
    finally:
        g.close()
        say("Guard ended - robots are stopped and stay stopped until a guard releases them again.")
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("guard", help="dead-man switch: robots deployed with --guard only move while this runs",
                       description=__doc__, formatter_class=__import__("argparse").RawDescriptionHelpFormatter)
    p.add_argument("robots", nargs="*", metavar="robot", help="default: all registered robots")
    p.set_defaults(func=cmd_guard)
