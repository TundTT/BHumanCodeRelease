"""The dashboard against stand-in robots, to try the page in a browser without robots or a simulator build.

    py -3 Util/bh/tests/dashdemo.py [PORT]        (default 8097), then open http://127.0.0.1:PORT/?t=demo

Alice (yours, running, guard armed), Bob (claimed by Dana, crash report, low battery), Carl (offline) are the
stand-ins of fakerobot.py in a temporary directory, so every button runs its real code against them and
nothing can reach a real robot or change this checkout. A fake GameController sends packets to 127.0.0.1;
stop the demo before `bh test`, whose UDP tests need the same ports. For practice with simulated bodies use
`bh dash --practice` (needs `bh build sim`).
"""
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

PORT = int(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PORT", 8097))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bhtool import cli, core, dash, gamecontroller as gc, guard  # noqa: E402
from tests import fakerobot  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="bhdemo"))
bash = fakerobot.find_bash(tmp)
repo = fakerobot.make_repo(tmp)
tools = fakerobot.make_local_tools(tmp)
alice = fakerobot.make_robot(tmp, "Alice", "192.168.89.14")
bob = fakerobot.make_robot(tmp, "Bob", "192.168.89.1")
os.environ.update({"FAKE_ROBOTS": (tmp / "robots").as_posix(), "FAKE_BASH": bash, "BH_BASH": '"%s"' % bash,
                   "BH_SSH": '"%s" "%s"' % (sys.executable, tools / "fake_ssh.py"),
                   # the shell's own directory too: started outside Git Bash, PATH lacks which/dirname
                   "PATH": os.pathsep.join([str(tools), os.environ["PATH"], os.path.dirname(bash)])})
core.ROOT = repo
core.tcp_open = lambda ip, port=22, timeout=0.8: (tmp / "robots" / ip).is_dir() and not (tmp / "robots" / ip / "offline").exists()
core.ssh_key = lambda: repo / "Install" / "Keys" / "id_rsa_b-human"
for argv in (["robot", "add", "Alice", "--id", "14"], ["robot", "add", "Bob", "--id", "1"], ["robot", "add", "Carl", "--id", "7"],
             ["deploy", "Alice:1", "Bob:2", "--no-build", "--guard", "--led", "test"], ["robot", "claim", "Alice", "--as", "Tund", "--note", "walk test"]):
    cli.main(argv)
(bob / "run/user/1000/internalState.txt").write_text("0.31 90")
(bob / "home/booster/bhdump.log").write_text("crash")
now = int(time.time())
(alice / "run/user/1000/ledState.txt").write_text("0 1 0")            # green: playing
(bob / "run/user/1000/ledState.txt").write_text("2 0 2")               # magenta, slow blink below
(bob / "run/user/1000/ledState.txt").write_text("1 0 0")               # red: penalized
(bob / "home/booster/.bh-claim").write_text("Dana\t%d\t%d\tintercept policy test\n" % (now + 3 * 3600, now - 3000))
os.environ["BH_USER"] = "Tund"
(tmp / "robots/192.168.89.1/service.state").write_text("active")
(tmp / "robots/192.168.89.14/service.state").write_text("active")

g = guard.Guard(["127.0.0.1"])
g.start()
tgc = gc.TestGameController.__new__(gc.TestGameController)  # no broadcasting from a demo: fake the controller


class FakeGC:
    def __init__(self):
        self.g = gc.GameData(state=3, secs_remaining=431, players_per_team=5)
        self.g.teams[0].number, self.g.teams[1].number = 89, 5
        self.g.teams[0].score = 2
        self.g.teams[0].players[1] = gc.Player(gc.PENALTY_PICK_UP, 31, 0)

    def set_state(self, name): self.g.state = gc.STATES.index(name)
    def set_stopped(self, s): self.g.stopped = int(s)
    def penalize(self, player, penalty=6): self.g.teams[0].players[player - 1] = gc.Player(penalty, 45 if penalty else 0, 0)
    def goal(self, own): self.g.teams[0 if own else 1].score += 1
    def reply_snapshot(self):
        return {1: (time.time(), "192.168.89.14", gc.ReturnData(1, 89, 0, (-1500.0, 320.0, 0.4), 1.2, (800.0, -100.0)))}
    def stop(self): pass


fake = FakeGC()


def feed():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    while True:
        fake.g.packet_number += 1
        s.sendto(gc.encode(fake.g), ("127.0.0.1", gc.DATA_PORT))
        time.sleep(0.5)


threading.Thread(target=feed, daemon=True).start()
d = dash.Dashboard(core.resolve_targets([]), 2.0, False, fake, g)
d.token = "demo"
d.me = "Tund"
server = ThreadingHTTPServer(("127.0.0.1", PORT), dash.make_handler(d))
threading.Thread(target=d.poll_loop, daemon=True).start()
print("http://127.0.0.1:%d/?t=demo" % PORT, flush=True)
try:
    server.serve_forever()
finally:
    d.close()
    shutil.rmtree(tmp, ignore_errors=True)
