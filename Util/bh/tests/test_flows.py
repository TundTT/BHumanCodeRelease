"""End-to-end tests: the real ``bh`` commands and the real Make/Common and
Install shell scripts, run against fake robots (see fakerobot.py).

Run from the repository root:   bh test      (add -v to see each step)
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bhtool import cli, core, dash, deploy, guard  # noqa: E402
from tests import fakerobot  # noqa: E402

ALICE, BOB = "192.168.89.14", "192.168.89.1"   # .1 is a prefix of .14 on purpose


class Flows(unittest.TestCase):
    """The methods run in name order and build on each other, like a day with a robot."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="bhtest"))
        bash = fakerobot.find_bash(cls.tmp)
        if bash is None:
            shutil.rmtree(cls.tmp, ignore_errors=True)
            raise unittest.SkipTest("no bash found that can run the repository scripts")
        cls.repo = fakerobot.make_repo(cls.tmp)
        cls.tools = fakerobot.make_local_tools(cls.tmp)
        cls.alice = fakerobot.make_robot(cls.tmp, "Alice", ALICE)
        cls.bob = fakerobot.make_robot(cls.tmp, "Bob", BOB)
        cls.saved_env = dict(os.environ)
        cls.saved = (core.ROOT, core.tcp_open, core.ssh_key, deploy.build_robot)
        os.environ.update({
            "FAKE_ROBOTS": (cls.tmp / "robots").as_posix(),
            "FAKE_BASH": bash,
            "BH_BASH": '"%s"' % bash,
            "BH_SSH": '"%s" "%s"' % (sys.executable, cls.tools / "fake_ssh.py"),
            "PATH": str(cls.tools) + os.pathsep + os.environ["PATH"],
        })
        core.ROOT = cls.repo
        core.tcp_open = lambda ip, port=22, timeout=0.8: (
            (cls.tmp / "robots" / ip).is_dir() and not (cls.tmp / "robots" / ip / "offline").exists())
        core.ssh_key = lambda: cls.repo / "Install" / "Keys" / "id_rsa_b-human"

    @classmethod
    def tearDownClass(cls):
        core.ROOT, core.tcp_open, core.ssh_key, deploy.build_robot = cls.saved
        os.environ.clear()
        os.environ.update(cls.saved_env)
        if os.environ.get("BH_TEST_KEEP"):
            print("kept for inspection: %s" % cls.tmp)
        else:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- helpers ----------------------------------------------------------
    def bh(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = cli.main(list(argv))
        return rc, out.getvalue()

    def ok(self, *argv):
        rc, out = self.bh(*argv)
        self.assertEqual(rc, 0, out)
        return out

    def fails(self, *argv):
        rc, out = self.bh(*argv)
        self.assertNotEqual(rc, 0, out)
        return out

    @staticmethod
    def robot_cfg(robot: Path) -> Path:
        return robot / "home" / "booster" / "Config"

    def settings(self, robot: Path) -> dict:
        return core.parse_cfg((self.robot_cfg(robot) / "settings.cfg").read_text())

    @staticmethod
    def service(robot: Path) -> str:
        f = robot / "service.state"
        return f.read_text().strip() if f.exists() else "inactive"

    @staticmethod
    def age(path: Path, seconds: float) -> None:
        t = time.time() - seconds
        os.utime(path, (t, t))

    def save_calibration_on_robot(self, robot: Path, name: str, text: str) -> Path:
        """What the robot does when a calibration is saved on it: a file newer than settings.cfg."""
        f = self.robot_cfg(robot) / "Robots" / name / "cameraCalibration.cfg"
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "w", newline="\n") as h:
            h.write(text)
        self.age(self.robot_cfg(robot) / "settings.cfg", 100)
        self.age(f, 50)
        return f

    # -- the day ----------------------------------------------------------
    def test_01_register_robots(self):
        self.ok("robot", "add", "Alice", "--id", "14")
        self.ok("robot", "add", "Bob", "--id", "1")
        robots = {r.name: r for r in core.load_robots()}
        self.assertEqual(robots["Alice"], core.Robot("Alice", ALICE, "10.0.89.14", "k1"))
        self.assertEqual(robots["Bob"].lan, BOB)
        self.assertIn("already exists", self.fails("robot", "add", "Alice", "--id", "20"))
        self.assertIn("already used by Alice", self.fails("robot", "add", "Carl", "--id", "14"))
        self.assertIn("names start with a letter", self.fails("robot", "add", "9lives", "--id", "9"))
        out = self.ok("robot", "list")
        self.assertIn("Alice", out)
        self.assertIn("cable", out)

    def test_02_status(self):
        stats = {s["name"]: s for s in json.loads(self.ok("status", "--json"))}
        a = stats["Alice"]
        self.assertTrue(a["reachable"])
        self.assertEqual((a["battery"], a["max_temp"], a["zones"]), (83, 47, [51.5]))
        self.assertEqual(a["service"], "inactive")
        self.assertEqual(a["settings"]["playerNumber"], "3")
        self.assertEqual(a["hardware"], "Booster K1")
        self.assertIn("12:00:01 Saying Low battery", a["log"])
        self.assertFalse(a["guard"])
        self.assertFalse(a["dump"])
        self.assertFalse(a["new_calibration"])
        self.assertIn("83%", self.ok("status", "Alice"))

    def test_03_scan_finds_foreign_software(self):
        out = self.fails("robot", "scan", "Alice")
        self.assertIn("evilbot.service", out)
        self.assertNotIn("pulseaudio", out)
        self.assertIn("ros.camera", out)
        self.assertNotIn("motion,", out)
        self.assertIn("old_team/start.sh", out)
        self.assertIn("ONNX Runtime with GPU support", out)
        self.assertIn("start scripts are installed", out)

    def test_04_quarantine_and_undo(self):
        log = self.alice / "systemctl.log"
        crontab = self.alice / "crontab"
        before = crontab.read_text()
        self.ok("robot", "quarantine", "Alice", "--all", "--cron", "--dry-run")
        self.assertFalse(log.exists())
        self.assertIn("must stay enabled", self.fails("robot", "quarantine", "Alice", "--unit", "bhuman.service", "-y"))
        self.assertIn("not a service name", self.fails("robot", "quarantine", "Alice", "--unit", "x; rm -rf /", "-y"))
        self.assertFalse(log.exists())
        out = self.ok("robot", "quarantine", "Alice", "--all", "--cron", "-y")
        self.assertIn("disabled evilbot.service", out)
        self.assertEqual(log.read_text().strip(), "disable --now evilbot.service")
        self.assertFalse(crontab.exists())
        self.assertEqual((self.alice / "home/booster/.bh-quarantine/units").read_text().strip(), "evilbot.service")
        out = self.ok("robot", "quarantine", "Alice", "--undo")
        self.assertIn("re-enabled evilbot.service", out)
        self.assertEqual(log.read_text().splitlines()[-1], "enable --now evilbot.service")
        self.assertEqual(crontab.read_text(), before)

    def test_05_deploy_rescues_and_arms_guard(self):
        calib = self.save_calibration_on_robot(self.alice, "Alice", "cameraCalibration = robot-side;\n")
        engine = self.robot_cfg(self.alice) / "NeuralNets" / "TensorRTCache" / "built_on_robot.engine"
        engine.parent.mkdir(parents=True, exist_ok=True)
        engine.write_bytes(b"\x00engine\xff" * 100)
        (self.robot_cfg(self.alice) / "stale_file_from_old_code.cfg").write_text("x")
        out = self.ok("deploy", "Alice:2", "--no-build", "--guard", "--led", "test", "--team", "12")
        self.assertIn("rescued cameraCalibration.cfg", out)
        self.assertIn("verified (team 12, player 2", out)
        cfg = self.robot_cfg(self.alice)
        # Robot-only files survived the rsync --del because they were pulled into the repository first.
        local_calib = self.repo / "Config/Robots/Alice/cameraCalibration.cfg"
        self.assertEqual(local_calib.read_text(), "cameraCalibration = robot-side;\n")
        self.assertEqual(calib.read_text(), "cameraCalibration = robot-side;\n")
        self.assertEqual((self.repo / "Config/NeuralNets/TensorRTCache/built_on_robot.engine").read_bytes(),
                         b"\x00engine\xff" * 100)
        self.assertEqual(engine.read_bytes(), b"\x00engine\xff" * 100)
        self.assertFalse((cfg / "stale_file_from_old_code.cfg").exists())
        # The robot runs what we sent, with the requested settings.
        self.assertEqual((cfg / "bhuman").read_text(), "fake robot binary (Develop)\n")
        s = self.settings(self.alice)
        self.assertEqual((s["playerNumber"], s["teamNumber"], s["scenario"]), ("2", "12", "Default"))
        self.assertTrue((cfg / "libonnxruntime.so.1").exists())
        self.assertTrue((cfg / "libonnxruntime_providers_tensorrt.so").exists())
        self.assertIn("mode = test;", (cfg / "Scenarios/Default/ledHandler.cfg").read_text())
        self.assertIn("mode = automatic;", (self.repo / "Config/Scenarios/Default/ledHandler.cfg").read_text())
        # Guard: a robot-side override of the thread configuration, nothing in the repository.
        threads = (cfg / "Robots/Alice/threads.cfg").read_text()
        self.assertIn("\n      {representation = EmergencyButtonState; provider = EmergencyButtonProvider;},", threads)
        self.assertNotIn("//{representation = EmergencyButtonState", threads)
        self.assertNotIn("EmergencyButtonState,\n", threads)
        self.assertFalse((self.repo / "Config/Robots/Alice/threads.cfg").exists())
        self.assertEqual(self.service(self.alice), "active")
        bin_log = (self.alice / "bin.log").read_text()
        self.assertIn("setvolume 100", bin_log)
        self.assertNotIn("setprofile", bin_log)
        a = json.loads(self.ok("status", "Alice", "--json"))[0]
        self.assertEqual((a["guard"], a["led"], a["service"], a["new_calibration"]), (True, "test", "active", False))
        self.assertIn("laptop guard is armed", self.ok("status", "Alice"))

    def test_06_plain_deploy_removes_guard(self):
        out = self.ok("deploy", "Alice", "--no-build", "--no-restart")
        self.assertIn("bh start Alice", out)
        cfg = self.robot_cfg(self.alice)
        self.assertFalse((cfg / "Robots/Alice/threads.cfg").exists())
        self.assertIn("mode = automatic;", (cfg / "Scenarios/Default/ledHandler.cfg").read_text())
        self.assertEqual(self.service(self.alice), "inactive")
        s = self.settings(self.alice)
        self.assertEqual((s["playerNumber"], s["teamNumber"]), ("3", "89"))
        self.assertTrue((cfg / "NeuralNets/TensorRTCache/built_on_robot.engine").exists())
        self.assertTrue((cfg / "Robots/Alice/cameraCalibration.cfg").exists())

    def test_07_calibration_pull(self):
        self.assertIn("same", self.ok("calib", "status", "Alice"))
        self.save_calibration_on_robot(self.alice, "Alice", "cameraCalibration = second;\n")
        out = self.ok("calib", "status", "Alice")
        self.assertIn("DIFFERENT", out)
        self.assertIn("bh calib pull Alice", out)
        self.assertIn("not in the repository yet", self.ok("status", "Alice"))
        self.assertIn("pulled cameraCalibration.cfg", self.ok("calib", "pull", "Alice"))
        local = self.repo / "Config/Robots/Alice/cameraCalibration.cfg"
        self.assertEqual(local.read_text(), "cameraCalibration = second;\n")
        backups = list((local.parent / ".calibration-backup").iterdir())
        self.assertEqual([b.read_text() for b in backups], ["cameraCalibration = robot-side;\n"])
        out = self.ok("calib", "status", "Alice")
        self.assertNotIn("DIFFERENT", out)
        self.assertNotIn("calib pull", out)
        self.assertIn("already has", self.ok("calib", "pull", "Alice"))

    def test_08_overwrite_calibration_on_request(self):
        robot_side = self.save_calibration_on_robot(self.alice, "Alice", "cameraCalibration = unwanted;\n")
        out = self.ok("deploy", "Alice", "--no-build", "--no-restart", "--overwrite-calibration")
        self.assertIn("will be overwritten", out)
        self.assertEqual(robot_side.read_text(), "cameraCalibration = second;\n")
        self.assertEqual((self.repo / "Config/Robots/Alice/cameraCalibration.cfg").read_text(),
                         "cameraCalibration = second;\n")

    def test_09_policy_push(self):
        self.assertIn("already has these policies", self.ok("policy", "push", "Alice", "--dry-run"))
        walk_cfg = core.find_config("walkingEngine.cfg", "k1")
        name = core.cfg_get(core.read_cfg(walk_cfg), "walkNeuronalNetworkParameters.modelName")
        on_robot = self.robot_cfg(self.alice) / "NeuralNets" / "Walk" / name
        original = on_robot.read_bytes()
        on_robot.write_bytes(b"an older policy")
        out = self.ok("policy", "push", "Alice", "--dry-run")
        self.assertIn("NeuralNets/Walk/" + name, out)
        self.assertEqual(on_robot.read_bytes(), b"an older policy")
        self.assertIn("skipped", self.ok("policy", "push", "Alice"))      # no terminal = no consent
        self.assertEqual(on_robot.read_bytes(), b"an older policy")
        self.assertIn("policies updated", self.ok("policy", "push", "Alice", "-y"))
        self.assertEqual(on_robot.read_bytes(), original)
        self.assertEqual(self.service(self.alice), "active")

    def test_10_service_control_and_logs(self):
        self.ok("stop", "Alice")
        self.assertEqual(self.service(self.alice), "inactive")
        self.ok("start", "Alice")
        self.assertEqual(self.service(self.alice), "active")
        self.fails("restart", "Alice")                                    # asks first; no terminal = no
        self.ok("restart", "Alice", "-y")
        self.ok("stop")                                                   # no names = every robot
        self.assertEqual((self.service(self.alice), self.service(self.bob)), ("inactive", "inactive"))
        self.assertIn("Saying Low battery", self.ok("logs", "Alice"))

    def test_11_match(self):
        def fake_build(config):
            fakerobot._write(deploy.binary_path(config), "fake robot binary (%s)\n" % config)
        deploy.build_robot = fake_build
        self.assertIn("needs its player number", self.fails("match", "Alice", "Bob:2"))
        self.assertIn("same player number", self.fails("match", "Alice:2", "Bob:2"))
        state = self.bob / "var/volatile/tmp/internalState.txt"
        state.write_text("0.10 47")
        out = self.fails("match", "Alice:1", "Bob:2", "--profile", "HSL_A")
        self.assertIn("blocking problem", out)
        self.assertFalse((self.robot_cfg(self.bob) / "bhuman").exists())
        state.write_text("0.83 47")
        out = self.ok("match", "Alice:1", "Bob:2", "--profile", "HSL_A")
        self.assertIn("Match checklist", out)
        for robot, player in ((self.alice, "1"), (self.bob, "2")):
            cfg = self.robot_cfg(robot)
            self.assertEqual((cfg / "bhuman").read_text(), "fake robot binary (Release)\n")
            self.assertEqual(self.settings(robot)["playerNumber"], player)
            self.assertIn("mode = match;", (cfg / "Scenarios/Default/ledHandler.cfg").read_text())
            self.assertFalse((cfg / "Robots/Alice/threads.cfg").exists())
            self.assertEqual(self.service(robot), "active")
            self.assertIn("setprofile HSL_A", (robot / "bin.log").read_text())

    def test_12_failures_are_reported(self):
        self.assertIn("no Debug build", self.fails("deploy", "Alice", "-c", "Debug", "--no-build"))
        self.assertIn("unknown scenario", self.fails("deploy", "Alice", "--no-build", "--scenario", "Nope"))
        self.assertIn("unknown wireless profile", self.fails("deploy", "Alice", "--no-build", "--profile", "Nope"))
        self.assertIn("not a registered robot", self.fails("deploy", "192.168.89.77", "--no-build"))
        self.assertIn("unknown robot", self.fails("deploy", "Zed", "--no-build"))
        self.assertIn("must be 1..20", self.fails("deploy", "Alice:21", "--no-build"))
        # A copy that fails must not look like a success - neither for one robot nor for several.
        (self.bob / "rsync-fails").write_text("")
        try:
            self.fails("deploy", "Bob:2", "--no-build")
            self.fails("deploy", "Alice:1", "Bob:2", "--no-build")
        finally:
            (self.bob / "rsync-fails").unlink()
        self.ok("deploy", "Alice:1", "Bob:2", "--no-build", "--no-restart")
        self.assertEqual((self.robot_cfg(self.bob) / "bhuman").read_text(), "fake robot binary (Develop)\n")
        # A robot that is switched off.
        (self.bob / "offline").write_text("")
        try:
            self.assertIn("not reachable", self.fails("deploy", "Bob", "--no-build"))
            self.assertIn("OFFLINE", self.fails("status"))
        finally:
            (self.bob / "offline").unlink()
        # A policy that does not fit stops the deploy before anything is touched.
        walk_cfg = core.find_config("walkingEngine.cfg", "k1")
        text = core.read_exact(walk_cfg)
        n_in = core.cfg_get(core.parse_cfg(text), "walkNeuronalNetworkParameters.numInput")
        core.write_exact(walk_cfg, core.cfg_replace(text, "walkNeuronalNetworkParameters.numInput", str(int(n_in) + 1)))
        ssh_log = (self.alice / "ssh.log").read_text()
        try:
            self.assertIn("policy problems", self.fails("deploy", "Alice", "--no-build"))
            self.assertEqual((self.alice / "ssh.log").read_text(), ssh_log)
        finally:
            core.write_exact(walk_cfg, text)
        self.ok("policy", "check")

    def test_13_dry_run_touches_nothing(self):
        before = (self.alice / "service.state").read_text()
        binary = (self.robot_cfg(self.alice) / "bhuman").read_bytes()
        fakerobot._write(deploy.binary_path("Develop"), "a newer build\n")
        self.assertIn("Dry run", self.ok("deploy", "Alice", "--no-build", "--dry-run"))
        self.assertEqual((self.robot_cfg(self.alice) / "bhuman").read_bytes(), binary)
        self.assertEqual((self.alice / "service.state").read_text(), before)

    def test_14_dashboard(self):
        from http.server import ThreadingHTTPServer
        import threading
        targets = core.resolve_targets([])
        d = dash.Dashboard(targets, interval=0.5, passive=False, test_gc=None, guard=None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), dash.make_handler(d))
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        threading.Thread(target=d.poll_loop, daemon=True).start()
        base = "http://127.0.0.1:%d" % port

        def get(path):
            with urllib.request.urlopen(base + path, timeout=10) as r:
                return r.read()

        def post(body):
            req = urllib.request.Request(base + "/api/action", data=json.dumps(body).encode(), method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        try:
            for path in ("/", "/api/state", "/api/state?t=wrong"):
                with self.assertRaises(urllib.error.HTTPError) as e:
                    get(path)
                self.assertEqual(e.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as e:
                post({"action": "stop_all", "token": "wrong"})
            self.assertEqual(e.exception.code, 403)
            self.assertIn(b"<title>", get("/?t=" + d.token))
            self.ok("start", "Alice")
            deadline = time.time() + 30
            alice = {}
            while time.time() < deadline:
                state = json.loads(get("/api/state?t=" + d.token))
                alice = next(r for r in state["robots"] if r["name"] == "Alice")
                if alice.get("reachable") and alice.get("service") == "active":
                    break
                time.sleep(0.3)
            self.assertEqual((alice.get("battery"), alice.get("service")), (83, "active"))
            self.assertTrue(any(level == "ok" for level, _ in alice["assess"]))
            self.assertEqual(state["legend"], dash.LED_LEGEND)
            self.assertIn("ok", post({"action": "stop", "robot": "Alice", "token": d.token}))
            deadline = time.time() + 30
            while time.time() < deadline and self.service(self.alice) != "inactive":
                time.sleep(0.2)
            self.assertEqual(self.service(self.alice), "inactive")
            with self.assertRaises(urllib.error.HTTPError) as e:
                post({"action": "guard", "released": True, "token": d.token})
            self.assertEqual(e.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            d.close()


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.rx.bind(("127.0.0.1", guard.PORT))
        except OSError:
            self.rx.close()
            self.skipTest("UDP port %d is in use" % guard.PORT)
        self.rx.settimeout(2)

    def tearDown(self):
        self.rx.close()

    def drain(self):
        self.rx.setblocking(False)
        try:
            while True:
                self.rx.recv(64)
        except OSError:
            pass
        self.rx.settimeout(2)

    def test_heartbeat_and_stop(self):
        g = guard.Guard(["127.0.0.1", "127.0.0.1", ""])
        self.assertEqual(g.ips, ["127.0.0.1"])
        g.start()
        try:
            self.assertEqual(self.rx.recv(64), b"emergency\x00")        # starts stopped
            g.set_released(True)
            time.sleep(0.5)
            self.drain()
            self.assertEqual(self.rx.recv(64), b"noemergency\x00")
            g.set_released(False)
            time.sleep(0.5)
            self.drain()
            self.assertEqual(self.rx.recv(64), b"emergency\x00")
            g.set_released(True)
            time.sleep(0.3)
        finally:
            g.close()
        self.drain()
        with self.assertRaises(OSError):
            self.rx.recv(64)                                           # silence after close = robot stops

    def test_dashboard_dead_man(self):
        import threading
        g = guard.Guard(["127.0.0.1"])
        g.start()
        d = dash.Dashboard([], interval=0.3, passive=True, test_gc=None, guard=g)
        threading.Thread(target=d.poll_loop, daemon=True).start()
        try:
            d.state()                                  # a browser tab asked just now
            d.action({"action": "guard", "released": True})
            self.assertTrue(g.released)
            for _ in range(8):                         # while the page keeps polling, the guard stays released
                time.sleep(0.25)
                d.state()
            self.assertTrue(g.released)
            time.sleep(3.5)                            # the tab is gone
            self.assertFalse(g.released)
        finally:
            d.close()


class TestGameControllerLoop(unittest.TestCase):
    """The practice GameController and the listener, talking to each other on this computer only."""

    def test_packets_and_replies(self):
        from bhtool import gamecontroller as gc
        saved = gc.broadcast_addresses
        gc.broadcast_addresses = lambda: []            # a test must not send anything into the real network
        try:
            try:
                listener = gc.Listener()
                controller = gc.TestGameController(89, 0, 5, ["127.0.0.1"])
            except core.BhError as e:
                self.skipTest(str(e))
            self.assertEqual(controller.destinations, ["127.0.0.1"])
            controller.start()
            try:
                controller.set_state("playing")
                controller.penalize(2)
                controller.goal(True)                  # goal -> ready, kick-off for the opponent
                deadline = time.time() + 5
                g = None
                while time.time() < deadline:
                    g = listener.snapshot()[0]
                    if g is not None and g.teams[0].score == 1:
                        break
                    time.sleep(0.1)
                self.assertIsNotNone(g)
                self.assertEqual((gc.STATES[g.state], g.teams[0].number, g.teams[0].score, g.kicking_team),
                                 ("ready", 89, 1, 0))
                self.assertEqual(g.teams[0].players[1].penalty, gc.PENALTY_PICK_UP)
                self.assertEqual(g.teams[0].players[5].penalty, gc.PENALTY_SUBSTITUTE)
                robot = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                reply = gc.ReturnData(3, 89, 1, (1000.0, -500.0, 0.5), 2.0, (300.0, 0.0))
                robot.sendto(gc.encode_return(reply), ("127.0.0.1", gc.RETURN_PORT))
                robot.sendto(gc.encode_return(gc.ReturnData(4, 12, 0, (0.0, 0.0, 0.0), 0.0, (0.0, 0.0))),
                             ("127.0.0.1", gc.RETURN_PORT))     # another team's robot is ignored
                robot.close()
                deadline = time.time() + 5
                while time.time() < deadline and 3 not in controller.reply_snapshot():
                    time.sleep(0.1)
                replies = controller.reply_snapshot()
                self.assertEqual(sorted(replies), [3])
                self.assertEqual(replies[3][2], reply)
            finally:
                controller.stop()
                listener.close()
        finally:
            gc.broadcast_addresses = saved


if __name__ == "__main__":
    unittest.main()
