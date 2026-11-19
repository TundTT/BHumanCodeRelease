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
        cls.bob = fakerobot.make_robot(cls.tmp, "Bob", BOB, volatile=True)
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

    def test_03b_scan_says_whether_the_software_can_run(self):
        # Alice has the layout of a stock Jetson (no /var/volatile), Bob the one the installer creates.
        out = self.fails("robot", "scan", "Alice")
        self.assertIn("Will it run?", out)
        self.assertIn("status file can be written (/run/user/1000)", out)
        self.assertIn("firmware 1.7.1.1 is the tested one", out)
        self.assertNotIn("[FAIL]", out)
        self.assertIn("VERDICT", out)
        self.assertIn("status file can be written (/var/volatile/tmp)", self.fails("robot", "scan", "Bob"))

    def test_03c_scan_finds_what_stops_a_robot_that_looks_installed(self):
        # What a robot with another team's build looked like: nothing restarts the software after a
        # battery change, the status file has no directory, the camera stream is stuck, sudo is not allowed.
        home = self.alice / "home/booster"
        loop = home / "logs/bhumand_(2).log"
        dropin = home / ".config/systemd/user/bhuman.service.d/controls.conf"
        info = self.alice / "opt/booster/version.txt"
        saved_state = (self.alice / "run/user/1000/internalState.txt").read_text()
        saved_info = info.read_text()
        fakerobot._write(loop, "".join("12:00:%02d [cam_stream_receiver] unexpected calib size 858 (expected 490)"
                                       " - resyncing\n" % (i % 60) for i in range(400)))
        fakerobot._write(dropin, "[Service]\nExecStartPre=sudo /usr/local/sbin/bhuman-control-routing bhuman\n"
                                 "TimeoutStartSec=600\n")
        fakerobot._write(self.alice / "service.enabled", "disabled\n")
        fakerobot._write(self.alice / "nosudo", "")
        fakerobot._write(info, saved_info + "\nVersion: 1.8.1.2-release-02884-2026-10-15-global\n"
                               "Install time: Mon Nov 9 2026\n")
        shutil.rmtree(self.alice / "run")
        try:
            out = self.fails("robot", "scan", "Alice")
            self.assertIn("[FAIL] camera stream is stuck in a loop: 400 times", out)
            self.assertIn("unexpected calib size 858 (expected 490)", out)
            self.assertIn("bhuman.service is disabled", out)
            self.assertIn("controls.conf: ExecStartPre=sudo /usr/local/sbin/bhuman-control-routing bhuman", out)
            self.assertIn("[FAIL] ", out.split("controls.conf", 1)[1])         # sudo before every start, sudo not allowed
            self.assertIn("no writable directory for the status file", out)
            self.assertIn("firmware 1.8.1.2 has not been tested", out)
            self.assertIn("VERDICT: the software will NOT run properly", out)
            stats = {s["name"]: s for s in json.loads(self.ok("status", "--json"))}
            self.assertIsNone(stats["Alice"]["battery"])                      # what the dashboard showed
        finally:
            for path in (loop, dropin, self.alice / "service.enabled", self.alice / "nosudo"):
                path.unlink()
            fakerobot._write(info, saved_info)
            fakerobot._write(self.alice / "run/user/1000/internalState.txt", saved_state)

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

    def test_09b_sharing_a_robot(self):
        """Somebody is using the robot: others are told so, and what was on it can be put back."""
        home = self.alice / "home" / "booster"
        cfg = self.robot_cfg(self.alice)
        out = self.ok("robot", "claim", "Alice", "--as", "Dana", "--note", "walk 'test'")
        self.assertIn("in use by Dana", out)
        self.assertTrue((home / ".bh-claim").read_text().startswith("Dana\t"))
        self.assertIn("Dana", self.ok("status", "Alice"))
        status = json.loads(self.ok("status", "Alice", "--json"))[0]
        self.assertEqual((status["claim"]["who"], status["claim"]["note"], status["claim"]["expired"]),
                         ("Dana", "walk test", False))
        self.ok("robot", "claim", "Alice", "--as", "dana", "--note", "walk test")     # renewing one's own claim
        os.environ["BH_USER"] = "Eve"
        try:
            binary = (cfg / "bhuman").read_bytes()
            fakerobot._write(deploy.binary_path("Develop"), "Eve's build\n")
            out = self.fails("deploy", "Alice", "--no-build")
            self.assertIn("in use by dana", out)
            self.assertIn("walk test", out)
            self.assertEqual((cfg / "bhuman").read_bytes(), binary)
            self.assertEqual(self.service(self.alice), "active")
            self.assertIn("already in use by dana", self.fails("robot", "claim", "Alice"))
            self.assertIn("not by you", self.fails("robot", "release", "Alice"))
            self.assertIn("in use by dana", self.fails("robot", "restore", "Alice", "-y"))
            walk_cfg = core.find_config("walkingEngine.cfg", "k1")
            name = core.cfg_get(core.read_cfg(walk_cfg), "walkNeuronalNetworkParameters.modelName")
            on_robot = cfg / "NeuralNets" / "Walk" / name
            original = on_robot.read_bytes()
            on_robot.write_bytes(b"dana's policy")
            self.assertIn("in use by dana", self.fails("policy", "push", "Alice", "-y"))
            self.assertEqual(on_robot.read_bytes(), b"dana's policy")
            self.assertEqual(self.service(self.alice), "active")
            self.ok("policy", "push", "Alice", "--dry-run")                          # looking is always allowed
            on_robot.write_bytes(original)
            self.ok("stop", "Alice")                                                 # a stop never asks who owns it
            self.assertIn("continuing anyway", self.ok("deploy", "Alice", "--no-build", "--ignore-claim"))
            self.assertEqual((cfg / "bhuman").read_text(), "Eve's build\n")
            self.assertTrue((home / ".bh-claim").exists())                           # a deploy does not remove it
            # A claim that nobody released ends by itself.
            (home / ".bh-claim").write_text("Dana\t%d\t%d\tforgotten\n" % (time.time() - 60, time.time() - 9 * 3600))
            self.ok("deploy", "Alice", "--no-build", "--dry-run")
            self.ok("robot", "claim", "Alice", "--hours", "1")
            self.assertTrue((home / ".bh-claim").read_text().startswith("Eve\t"))
            self.assertIn("free again", self.ok("robot", "release", "Alice"))
            self.assertFalse((home / ".bh-claim").exists())
            self.assertIn("was not marked", self.ok("robot", "release", "Alice"))
        finally:
            del os.environ["BH_USER"]
        # Backup, replace, restore.
        unit = home / ".config" / "systemd" / "user" / "bhuman.service"
        fakerobot._write(unit, "their unit\n")
        fakerobot._write(cfg / "teammate.cfg", "their work\n")
        out = self.ok("robot", "backup", "Alice", "--label", "Dana's build", "--as", "Dana")
        self.assertIn("bh robot restore Alice", out)
        first = sorted(p.stem for p in (home / ".bh-backups").glob("*.tar"))[0]
        listing = self.ok("robot", "backups", "Alice")
        self.assertIn(first, listing)
        self.assertIn("Danas build", listing)
        self.assertEqual(json.loads(self.ok("status", "Alice", "--json"))[0]["backups"], 1)
        fakerobot._write(deploy.binary_path("Develop"), "my experiment\n")
        out = self.ok("deploy", "Alice", "--no-build", "--backup")                   # a second one, on the way
        self.assertIn("Backing up", out)
        self.assertEqual(len(list((home / ".bh-backups").glob("*.tar"))), 2)
        self.assertFalse((cfg / "teammate.cfg").exists())                            # the deploy replaced Config
        fakerobot._write(unit, "my unit\n")
        fakerobot._write(unit.parent / "bhuman.service.d" / "mine.conf", "[Service]\n")
        self.assertEqual(self.service(self.alice), "active")
        self.fails("robot", "restore", "Alice", first)                               # asks first; no terminal = no
        self.assertFalse((cfg / "teammate.cfg").exists())
        self.assertIn("is not a backup id", self.fails("robot", "restore", "Alice", "../../etc", "-y"))
        self.assertIn("does not exist", self.fails("robot", "restore", "Alice", "20200101-000000", "-y"))
        self.assertEqual(self.service(self.alice), "active")
        self.assertIn("back in place", self.ok("robot", "restore", "Alice", first, "-y"))
        self.assertEqual((cfg / "teammate.cfg").read_text(), "their work\n")
        self.assertEqual((cfg / "bhuman").read_text(), "Eve's build\n")
        self.assertEqual(unit.read_text(), "their unit\n")
        self.assertFalse((unit.parent / "bhuman.service.d" / "mine.conf").exists())
        self.assertEqual(self.service(self.alice), "inactive")                       # stopped, and not started again
        self.assertFalse(list((home / ".bh-backups").glob("replaced.*")))
        # A damaged backup is refused before anything is touched.
        newest = sorted((home / ".bh-backups").glob("*.tar"))[-1]
        newest.write_bytes(b"not an archive")
        self.assertIn("cannot be read", self.fails("robot", "restore", "Alice", "-y"))
        self.assertEqual((cfg / "teammate.cfg").read_text(), "their work\n")
        for backup in sorted(p.stem for p in (home / ".bh-backups").glob("*.tar")):
            self.ok("robot", "backups", "Alice", "--delete", backup)
        self.assertIn("no backups", self.ok("robot", "backups", "Alice"))
        self.assertIn("has no backup", self.fails("robot", "restore", "Alice", "-y"))
        unit.unlink()
        fakerobot._write(deploy.binary_path("Develop"), "fake robot binary (Develop)\n")
        self.ok("deploy", "Alice", "--no-build")

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

    def test_13_deploy_refuses_a_robot_named_differently(self):
        # Seen on a K1 with the factory hostname "robot" registered as Robot5: the robot software read
        # Config/Robots/robot/, so the laptop guard deployed into Config/Robots/Robot5/ was not in effect.
        hostname = self.alice / "bin" / "hostname"
        original = hostname.read_text()
        try:
            fakerobot._script(hostname, "#!/bin/bash\necho robot\n")
            binary = (self.robot_cfg(self.alice) / "bhuman").read_bytes()
            out = self.fails("deploy", "Alice", "--no-build", "--guard")
            self.assertIn("hostname 'robot'", out)
            self.assertIn("laptop guard", out)
            self.assertEqual((self.robot_cfg(self.alice) / "bhuman").read_bytes(), binary)   # nothing copied
        finally:
            fakerobot._script(hostname, original)
        self.assertIn("Dry run", self.ok("deploy", "Alice", "--no-build", "--dry-run"))

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

            # Jobs: the same commands as the command line, with their output in the page.
            def job(body):
                answer = post(dict(body, token=d.token))
                jid = answer["data"]["job"]
                deadline = time.time() + 120
                while time.time() < deadline:
                    out = json.loads(get("/api/job?t=%s&id=%d&since=0" % (d.token, jid)))
                    if out["state"] != "running":
                        return out["state"], "\n".join(out["text"])
                    time.sleep(0.2)
                self.fail("job %s did not finish" % body)

            os.environ["BH_USER"] = "Dana"
            d.me = "Dana"
            try:
                state, text = job({"action": "claim", "robot": "Alice", "note": "--force me", "hours": 2})
                self.assertEqual(state, "ok", text)
                self.assertIn("$ bh robot claim Alice", text)
                self.assertIn("--force me", (self.alice / "home/booster/.bh-claim").read_text().split("\t")[3])
                d.poll_now.set()
                deadline = time.time() + 30
                while time.time() < deadline:
                    alice = next(r for r in json.loads(get("/api/state?t=" + d.token))["robots"] if r["name"] == "Alice")
                    if alice.get("claim"):
                        break
                    time.sleep(0.3)
                self.assertEqual((alice["claim"]["who"], alice["claim"]["mine"]), ("Dana", True))
                state, text = job({"action": "deploy", "robot": "Alice", "config": "Develop", "dry_run": True})
                self.assertEqual(state, "ok", text)
                self.assertIn("Dry run", text)
                self.assertIn("--no-build", text)
            finally:
                os.environ.pop("BH_USER")
                d.me = "x"
            state, text = job({"action": "deploy", "robot": "Alice", "dry_run": True})     # somebody else
            self.assertEqual(state, "failed")
            self.assertIn("in use by Dana", text)
            with self.assertRaises(urllib.error.HTTPError) as e:                           # one job per robot
                post({"action": "scan", "robot": "Alice", "token": d.token})
                post({"action": "scan", "robot": "Alice", "token": d.token})
            self.assertIn(b"busy", e.exception.read())
            deadline = time.time() + 60
            while time.time() < deadline and any(j["state"] == "running" for j in json.loads(get("/api/state?t=" + d.token))["jobs"]):
                time.sleep(0.3)
            state, text = job({"action": "release", "robot": "Alice", "force": True})
            self.assertEqual(state, "ok", text)
            self.assertFalse((self.alice / "home/booster/.bh-claim").exists())
            for bad in ({"action": "deploy", "robot": "Zed"}, {"action": "deploy", "robot": "Alice", "config": "Fast"},
                        {"action": "deploy", "robot": "Alice", "scenario": "../x"}, {"action": "restore", "robot": "Alice", "backup": "../x"},
                        {"action": "policy_use", "slot": "walk", "model": "k1", "scope": "sim", "file": "../x.onnx"}):
                with self.assertRaises(urllib.error.HTTPError) as e:
                    post(dict(bad, token=d.token))
                self.assertEqual(e.exception.code, 400, bad)

            # Policies: upload, check the fit, use it for the simulated robot only.
            def upload(name, data):
                req = urllib.request.Request("%s/api/upload?t=%s&name=%s" % (base, d.token, name), data=data, method="POST")
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read())
            walk = core.find_config("walkingEngine.cfg", "k1")
            current = core.cfg_get(core.read_cfg(walk), "walkNeuronalNetworkParameters.modelName")
            data = (core.config_dir() / "NeuralNets" / "Walk" / current).read_bytes()
            for name, body in (("../evil.onnx", data), ("junk.onnx", b"not a network")):
                with self.assertRaises(urllib.error.HTTPError) as e:
                    upload(name, body)
                self.assertEqual(e.exception.code, 400)
            self.assertFalse((dash.upload_dir() / "junk.onnx").exists())
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(urllib.request.Request(base + "/api/upload?t=wrong&name=x.onnx", data=data, method="POST"))
            self.assertEqual(e.exception.code, 403)
            info = upload("TestWalk.onnx", data)["data"]
            self.assertIsNone(info["fits"]["k1"]["walk"])
            self.assertIn("inputs", info["fits"]["k1"]["kick"])
            meta = json.loads(get("/api/meta?t=" + d.token))
            self.assertEqual([u["name"] for u in meta["uploads"]], ["TestWalk.onnx"])
            self.assertIn("Default", meta["robots"]["Alice"]["walk"])
            state, text = job({"action": "policy_use", "slot": "kick", "model": "k1", "scope": "sim", "file": "TestWalk.onnx"})
            self.assertEqual(state, "failed")
            self.assertIn("expects", text)
            state, text = job({"action": "policy_use", "slot": "walk", "model": "k1", "scope": "sim", "file": "TestWalk.onnx"})
            self.assertEqual(state, "ok", text)
            sim = core.config_dir() / "Robots" / "SimulatedK1" / "walkingEngine.cfg"
            self.assertEqual(core.cfg_get(core.read_cfg(sim), "walkNeuronalNetworkParameters.modelName"), "TestWalk.onnx")
            self.assertEqual(core.cfg_get(core.read_cfg(walk), "walkNeuronalNetworkParameters.modelName"), current)
            k1_walk = next(s for s in json.loads(get("/api/meta?t=" + d.token))["policies"]["models"]["k1"] if s["key"] == "walk")
            self.assertEqual((k1_walk["sim"], k1_walk["robot"][:1]), (["TestWalk.onnx"], [current]))
            core.write_exact(sim, core.cfg_replace(core.read_exact(sim), "walkNeuronalNetworkParameters.modelName", current))
            (core.config_dir() / "NeuralNets" / "Walk" / "TestWalk.onnx").unlink()
        finally:
            server.shutdown()
            server.server_close()
            d.close()

    def test_14b_passive_dashboard_does_not_touch_robots(self):
        d = dash.Dashboard(core.resolve_targets([]), interval=1, passive=True, test_gc=None, guard=None)
        try:
            ssh_log = (self.alice / "ssh.log").read_text()
            for kind in ("deploy", "policy_push", "claim", "backup", "restore", "scan", "logs", "stop", "guard_heartbeat"):
                with self.assertRaises(core.BhError):
                    d.action({"action": kind, "robot": "Alice"})
            self.assertEqual((self.alice / "ssh.log").read_text(), ssh_log)
        finally:
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

    def test_dashboard_heartbeat_per_robot(self):
        """The page starts a heartbeat for one robot only when asked; the dead-man applies to it too.
        (Sent to this computer only: the test must not reach a real robot that has the test's address.)"""
        saved = guard.guard_ips
        guard.guard_ips = lambda robots: ["127.0.0.1"]
        robot = core.Target("127.0.0.1", core.Robot("Alice", "127.0.0.1", "127.0.0.1", "k1"))
        d = dash.Dashboard([robot], interval=0.3, passive=False, test_gc=None, guard=None)
        try:
            self.assertEqual(d.robot_guards, {})                      # nothing is sent before the user asks
            d.state()
            d.action({"action": "guard", "robot": "Alice", "released": True})
            self.assertTrue(d.robot_guards["Alice"].released)
            time.sleep(0.5)
            self.drain()
            self.assertEqual(self.rx.recv(64), b"noemergency\x00")
            d.last_ui_contact = time.time() - 5                       # the tab is gone
            d.dead_man()
            self.assertFalse(d.robot_guards["Alice"].released)
            time.sleep(0.3)
            self.drain()
            self.assertEqual(self.rx.recv(64), b"emergency\x00")
            d.action({"action": "guard_heartbeat", "robot": "Alice", "on": False})
            self.assertEqual(d.robot_guards, {})
            with self.assertRaises(core.BhError):
                d.action({"action": "guard", "robot": "Zed", "released": True})
        finally:
            guard.guard_ips = saved
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


class FakeSimulator:
    """Plays SimRobot's side of the practice link (ConsoleRoboCupCtrl::updatePracticeBridge): records the
    console commands and sends the game state and the first team's positions."""

    def __init__(self, port: int):
        from bhtool import gamecontroller as gc
        self.gc = gc
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", port))
        self.sock.settimeout(0.1)
        self.commands = []
        self.fallen = set()
        self.game = gc.GameData(players_per_team=3)
        self.game.teams[0].number, self.game.teams[1].number = 5, 70
        self.running = True
        import threading
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        last = 0.0
        while self.running:
            try:
                data, _ = self.sock.recvfrom(4096)
                self.commands.append(data.decode())
            except (socket.timeout, ConnectionResetError):   # Windows reports sends to a closed port here
                pass
            except OSError:
                break
            if time.time() - last > 0.2:
                last = time.time()
                self.sock.sendto(self.gc.encode(self.game), ("127.0.0.1", self.port + 1))
                for n in (1, 2, 3):
                    reply = self.gc.ReturnData(n, 5, int(n in self.fallen), (-1000.0 * n, 500.0, 0.5), 0.0, (800.0, 0.0))
                    self.sock.sendto(self.gc.encode_return(reply), ("127.0.0.1", self.port + 1))

    def close(self):
        self.running = False
        self.thread.join(2)
        self.sock.close()


class PracticeTest(unittest.TestCase):
    """bh dash --practice with a stand-in for SimRobot: what the buttons do to the simulated robots."""

    def wait_for(self, what, condition, d=None, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if d is not None:
                d.state()                          # the page asks once a second (dead man of the guard)
            if condition():
                return
            time.sleep(0.1)
        self.fail("timed out waiting for %s; commands: %s" % (what, self.sim.commands[-12:]))

    def sent(self, robot, command):
        return "robot %s\n%s" % (robot, command) in self.sim.commands

    def test_practice_dashboard(self):
        from bhtool import practice
        tmp = Path(tempfile.mkdtemp(prefix="bhpractice"))
        real = (core.ROOT, core.tcp_open, core.ssh_key, dict(os.environ))
        port = 10994
        try:
            self.sim = FakeSimulator(port)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
            self.skipTest("UDP port %d is in use" % port)
        lab = practice.PracticeLab("k1", "GameFast3v3", 3, "Develop", port=port, base=tmp / "practice",
                                   start_simulator=False)
        d = None
        try:
            lab.find_simulator()
            if fakerobot.find_bash(tmp) is None:
                self.skipTest("no bash found that can run the repository scripts")
            names = lab.prepare()
            self.assertEqual(names, ["Sim1", "Sim2", "Sim3"])
            self.assertNotEqual(core.ROOT, real[0])
            for r in core.load_robots():          # only loopback addresses: nothing can reach a real robot
                self.assertEqual((r.lan, r.wlan), (practice.address(int(r.name[3:])),) * 2)
            lab.start()
            d = dash.Dashboard(core.resolve_targets(names, prefer="lan"), interval=0.5, passive=False,
                               test_gc=lab.gc, guard=None, practice=lab)
            import threading
            threading.Thread(target=d.poll_loop, daemon=True).start()

            # the simulator appears: every robot gets the guard's module, nobody is stopped
            self.wait_for("set-up", lambda: all(self.sent("robot%d" % n, practice.emergency_command(False))
                                                 for n in (1, 2, 3)), d)
            self.assertTrue(self.sent("robot2", "for Cognition mr EmergencyButtonState EmergencyButtonProvider"))
            st = d.state()
            self.assertEqual(st["team"], 5)
            self.assertEqual(st["practice"]["robots"], names)
            self.assertEqual(st["game"]["teams"], [5, 70])

            # Stop -> limp; Hold STAND refused while stopped; Start -> still limp; Hold STAND -> stands up
            d.action({"action": "stop", "robot": "Sim1"})
            self.wait_for("unstiff", lambda: self.sent("robot1", practice.UNSTIFF), d)
            self.wait_for("Sim1 stopped", lambda: not lab.robot("Sim1").software_running(), d)
            with self.assertRaises(core.BhError):
                d.action({"action": "practice_stand", "robot": "Sim1"})
            count = self.sim.commands.count("robot robot1\n" + practice.UNSTIFF)
            d.action({"action": "start", "robot": "Sim1"})
            self.wait_for("unstiff after start", lambda: self.sim.commands.count("robot robot1\n" + practice.UNSTIFF) > count, d)
            self.sim.fallen.add(1)                 # it collapsed when it went limp
            time.sleep(0.5)
            d.action({"action": "practice_stand", "robot": "Sim1"})
            self.wait_for("stand up", lambda: self.sent("robot1", practice.STAND_UP), d)
            self.wait_for("set on its feet", lambda: self.sent("robot1", "mv -1000 500 537 0 0 29"), d)
            self.sim.fallen.clear()

            # the test GameController buttons drive the simulator's GameController
            d.action({"action": "gc_state", "state": "playing"})
            d.action({"action": "gc_penalize", "player": 2, "on": True})
            d.action({"action": "gc_goal", "own": True})
            self.wait_for("gc", lambda: "gc playing" in self.sim.commands and self.sent("robot2", "pr manual")
                          and "gc goal firstTeam" in self.sim.commands, d)
            with self.assertRaises(core.BhError):
                d.action({"action": "sim"})

            # the simulated robot's LED and position appear on the card
            led = tmp / "practice" / "sim" / "5-1"
            led.mkdir(parents=True)
            (led / "ledState.txt").write_text("0 1 0")
            self.wait_for("LED", lambda: any(s["name"] == "Sim1" and (s.get("led_view") or {}).get("colour") == "green"
                                             for s in d.state()["robots"]), d)
            sim1 = next(s for s in d.state()["robots"] if s["name"] == "Sim1")
            self.assertEqual(sim1["reply"]["pose"][:2], [-1000, 500])
            self.assertTrue(next(s for s in d.state()["robots"] if s["name"] == "Sim3")["claim"]["who"].startswith("Kim"))

            # deployed with the guard: the robot stands still until the page sends RUN, and again after STOP
            _, data = d.action({"action": "deploy", "robot": "Sim2", "player": 2, "guard": True, "build": True})
            self.wait_for("deploy", lambda: d.jobs[data["job"]].state != "running", d, timeout=120)
            self.assertEqual(d.jobs[data["job"]].state, "ok", "\n".join(d.jobs[data["job"]].lines))
            self.assertIn("--no-build", "\n".join(d.jobs[data["job"]].lines))
            self.wait_for("guard stop", lambda: lab.robot("Sim2").emergency is True, d)
            self.assertTrue(self.sent("robot2", practice.emergency_command(True)))
            d.action({"action": "guard", "robot": "Sim2", "released": True})
            self.wait_for("guard run", lambda: lab.robot("Sim2").emergency is False, d)
            d.action({"action": "guard", "robot": "Sim2", "released": False})
            self.wait_for("guard stop again", lambda: lab.robot("Sim2").emergency is True, d)
            self.assertFalse(lab.robot("Sim1").emergency)      # the others are not affected

            # STOP ALL: every robot limp and stopped
            d.action({"action": "stop_all"})
            self.wait_for("stop all", lambda: all(not r.software_running() and r.emergency for r in lab.robots), d)
        finally:
            if d is not None:
                d.close()
            else:
                lab.close()
            self.sim.close()
            self.assertEqual(core.ROOT, real[0])
            self.assertIs(core.tcp_open, real[1])
            self.assertEqual(os.environ.get("BH_SSH"), real[3].get("BH_SSH"))
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
