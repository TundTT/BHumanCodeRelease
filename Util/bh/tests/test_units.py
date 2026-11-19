"""Fast checks of the pure parts of bhtool, run against the real repository
files (configuration, policies, protocol header) so that they also notice
when the C++ side or the configuration drifts away from the tools."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bhtool import cli, core, deploy, gamecontroller as gc, onnxinfo, policy, remote, robots  # noqa: E402

REPO = Path(__file__).resolve().parents[3]


class Cfg(unittest.TestCase):
    def test_every_configuration_file_parses(self):
        files = [p for p in (REPO / "Config").rglob("*.cfg")]
        self.assertGreater(len(files), 100)
        for p in files:
            core.read_cfg(p)

    def test_values(self):
        cfg = core.parse_cfg('a = 1; // c\nb = { c = "x y"; d = [1, 2, {e = f;}]; };\n/* z */ g = h - 1000;')
        self.assertEqual(cfg, {"a": "1", "b": {"c": "x y", "d": ["1", "2", {"e": "f"}]}, "g": "h - 1000"})
        self.assertEqual(core.cfg_get(cfg, "b.c"), "x y")
        self.assertIsNone(core.cfg_get(cfg, "b.nope.x"))
        for broken in ("a = ;", "a = 1", "a = {b = 1;", "= 1;", "a = [1, 2;"):
            with self.assertRaises(core.BhError):
                core.parse_cfg(broken)

    def test_replace_keeps_formatting(self):
        text = 'a = 1; // keep\nb = {\n  name = "old.onnx"; // me too\n  n = 344;\n};\n'
        out = core.cfg_replace(text, "b.name", "new.onnx")
        self.assertEqual(out, text.replace("old.onnx", "new.onnx"))
        self.assertEqual(core.cfg_replace(text, "b.n", "345"), text.replace("344", "345"))
        with self.assertRaises(core.BhError):
            core.cfg_replace(text, "b.missing", "1")

    def test_search_path_matches_settings_cpp(self):
        c = REPO / "Config"
        got = [p.relative_to(c).as_posix() for p in core.search_path("k1", "Alice", "AnyPlaceDemo", "Field1")]
        self.assertEqual(got, ["Robots/Alice", "Locations/Field1/K1", "Locations/Field1", "Scenarios/AnyPlaceDemo/K1",
                               "Scenarios/AnyPlaceDemo", "Robots/Default/K1", "Robots/Default", "Locations/Default/K1",
                               "Locations/Default", "Scenarios/Default/K1", "Scenarios/Default", "."])
        self.assertEqual(len(core.search_path("t1")), 8)
        self.assertTrue(core.find_config("threads.cfg", "k1").as_posix().endswith("Scenarios/Default/K1/threads.cfg"))
        self.assertTrue(core.find_config("ledHandler.cfg", "k1").as_posix().endswith("Scenarios/Default/ledHandler.cfg"))

    def test_small_helpers(self):
        self.assertEqual(core.split_command('"C:\\Program Files\\Git\\bash.exe" -x'), ["C:\\Program Files\\Git\\bash.exe", "-x"])
        self.assertTrue(core.is_ip("192.168.10.102"))
        self.assertFalse(core.is_ip("192.168.10.300"))
        self.assertFalse(core.is_ip("Alice"))
        self.assertEqual(core.table([["a", 1], ["bbb", 22]], ["x", "y"]).splitlines(), ["x    y", "---  --", "a    1", "bbb  22"])


class Policies(unittest.TestCase):
    def test_shipped_policies_fit_their_configuration(self):
        found = policy.findings()
        self.assertGreater(len(found), 10)
        self.assertEqual([f for f in found if f.problem], [])
        sizes = {(policy.model_of(f.cfg), f.slot.key): f.detail for f in found}
        self.assertEqual(sizes[("k1", "walk")], "344 in, 13 out")
        self.assertEqual(sizes[("t1", "walk")], "365 in, 14 out")
        self.assertEqual(sizes[("k1", "getup")], "73 in, 22 out")
        self.assertEqual(sizes[("t1", "getup-fast")], "75 in, 23 out")

    def test_wrong_sizes_are_caught(self):
        walk = core.find_config("walkingEngine.cfg", "k1")
        name = core.cfg_get(core.read_cfg(walk), "walkNeuronalNetworkParameters.modelName")
        info = onnxinfo.describe(REPO / "Config" / "NeuralNets" / "Walk" / name)
        self.assertIsNone(policy.signature_problem(info, 344, 13))
        self.assertIn("expects 344 inputs", policy.signature_problem(info, 365, 13))
        self.assertIn("produces 13 outputs", policy.signature_problem(info, 344, 14))
        with self.assertRaises(onnxinfo.OnnxError):
            onnxinfo.describe_bytes(b"this is not a model")

    def test_cpp_still_reads_the_attributes_the_tool_checks(self):
        """If someone renames a parameter in C++, the policy check must not silently check nothing."""
        src = REPO / "Src"
        text = "".join(p.read_text(encoding="utf-8", errors="replace")
                       for d in ("Modules/MotionControl/WalkingEngine", "Modules/MotionControl/GetUpEngine")
                       for p in (src / d).rglob("*.h"))
        words = {w for s in policy.SLOTS for a in (s.attr, s.n_in, s.n_out) for w in a.split(".")}
        self.assertTrue({"walkNeuronalNetworkParameters", "ballNetworkParameters", "kickModel", "policyNameFast"} <= words)
        for word in sorted(words):
            self.assertRegex(text, r"\b%s\b" % word)

    def test_every_slot_is_configured(self):
        """A slot whose attribute is in no configuration file would be skipped by the check without a word
        (a new slot added to policy.SLOTS before its parameters are in the .cfg files, or a typo)."""
        for slot in policy.SLOTS:
            configured = [p for p in policy.cfg_files(slot.cfg) if core.cfg_get(core.read_cfg(p), slot.attr) is not None]
            self.assertTrue(configured, "slot %s: %s is in no copy of %s" % (slot.key, slot.attr, slot.cfg))
            for p in configured:
                cfg = core.read_cfg(p)
                for size in (slot.n_in, slot.n_out):
                    self.assertIsNotNone(policy._int(core.cfg_get(cfg, size)), "%s: %s missing" % (p, size))


class GameControllerProtocol(unittest.TestCase):
    def test_constants_match_the_c_header(self):
        header = (REPO / "Util/GameController/include/RoboCupGameControlData.h").read_text()
        define = lambda name: re.search(r"#define\s+%s\s+\"?([^\s\"]+)" % name, header).group(1)
        self.assertEqual(int(define("GAMECONTROLLER_DATA_PORT")), gc.DATA_PORT)
        self.assertEqual(int(define("GAMECONTROLLER_RETURN_PORT")), gc.RETURN_PORT)
        self.assertEqual(define("GAMECONTROLLER_STRUCT_HEADER").encode(), gc.HEADER)
        self.assertEqual(int(define("GAMECONTROLLER_STRUCT_VERSION")), gc.VERSION)
        self.assertEqual(define("GAMECONTROLLER_RETURN_STRUCT_HEADER").encode(), gc.RETURN_HEADER)
        self.assertEqual(int(define("GAMECONTROLLER_RETURN_STRUCT_VERSION")), gc.RETURN_VERSION)
        self.assertEqual(int(define("MAX_NUM_PLAYERS")), gc.MAX_PLAYERS)
        self.assertEqual(int(define("PENALTY_PICK_UP")), gc.PENALTY_PICK_UP)
        self.assertEqual(int(define("PENALTY_SUBSTITUTE")), gc.PENALTY_SUBSTITUTE)
        for i, state in enumerate(gc.STATES):
            self.assertEqual(int(define("STATE_" + state.upper())), i)
        self.assertEqual(gc.PACKET_SIZE, 18 + 2 * (10 + 3 * gc.MAX_PLAYERS))
        self.assertEqual(gc.RETURN_SIZE, 32)

    def test_round_trip(self):
        g = gc.GameData(packet_number=300, players_per_team=5, stopped=1, state=3, set_play=4, first_half=0,
                        kicking_team=89, secs_remaining=-12, secondary_time=30)
        g.teams[0].number, g.teams[0].score = 89, 3
        g.teams[1].number = 7
        g.teams[0].players[1] = gc.Player(gc.PENALTY_PICK_UP, 45, 1)
        data = gc.encode(g)
        self.assertEqual(len(data), gc.PACKET_SIZE)
        self.assertIsNone(gc.classify(data))
        d = gc.decode(data)
        self.assertEqual((d.packet_number, d.stopped, d.state, d.set_play, d.first_half, d.kicking_team,
                          d.secs_remaining, d.secondary_time), (44, 1, 3, 4, 0, 89, -12, 30))
        self.assertEqual(d.team(89).players[1], gc.Player(gc.PENALTY_PICK_UP, 45, 1))
        self.assertEqual(d.team(89).score, 3)
        self.assertIsNone(d.team(5))
        self.assertIn("playing (STOPPED)", d.summary())
        self.assertIn("version 19", gc.classify(data[:4] + bytes([19]) + data[5:]))
        self.assertIn("size", gc.classify(data[:-1]))
        self.assertIsNotNone(gc.classify(b"RGrt" + data[4:]))
        r = gc.ReturnData(2, 89, 1, (100.0, -200.0, 1.5), 3.0, (10.0, 20.0))
        self.assertEqual(gc.decode_return(gc.encode_return(r)), r)
        with self.assertRaises(ValueError):
            gc.decode_return(data)


class RobotStatus(unittest.TestCase):
    OUTPUT = """noise
@host Alice
@service active
@internal 0.35 91
@zone 51500
@zone 86000
@disk 1048576 97%
@logs 12
@dump yes
@newest Config/Robots/Alice/cameraCalibration.cfg
@settings teamNumber = 89; playerNumber = 4; scenario = Default; location = Default;
@wifi wlan0 55 -61
@load 1.5 1.2 1.0
@mem 41
@uptime 600
@binary 1700000000
@now 1700000600
@guard yes
@led test
@model Booster K1
@log 12:00:00 Saying Hello
@end
"""

    def test_parse_and_assess(self):
        s = remote.parse_status(self.OUTPUT)
        self.assertTrue(s["complete"])
        self.assertEqual((s["battery"], s["max_temp"], s["zones"], s["disk_free_gb"], s["disk_used_pct"]),
                         (35, 91, [51.5, 86.0], 1.0, 97))
        self.assertEqual(s["settings"]["playerNumber"], "4")
        self.assertEqual(s["wifi"], {"device": "wlan0", "quality": 55, "dbm": -61})
        self.assertTrue(s["new_calibration"] and s["guard"] and s["dump"])
        s.update(name="Alice", ip="1.2.3.4", model="k1", reachable=True)
        levels = remote.assess(s)
        text = " | ".join("%s:%s" % lt for lt in levels)
        self.assertIn("fail:battery 35%", text)            # K1 warns below 40 %
        self.assertIn("fail:hottest joint 91 C", text)
        self.assertIn("warn:computer at 86 C", text)
        self.assertIn("warn:only 1.0 GB free", text)
        self.assertIn("bh calib pull Alice", text)
        self.assertIn("laptop guard is armed", text)
        s["model"] = "t1"                                  # T1 warns below 20 %
        self.assertIn(("warn", "battery 35% - change it before a game"), remote.assess(s))
        self.assertEqual(remote.assess({"reachable": False, "error": "nope"}), [("fail", "nope")])
        self.assertFalse(remote.parse_status("@host x\n")["complete"])
        self.assertTrue(remote.parse_status("@log x\n\n@end\n")["complete"])
        self.assertIsNone(remote.parse_status("@internal \n@end")["battery"])
        self.assertEqual(remote.describe({"name": "A", "ip": "1.2.3.4", "reachable": False, "error": "x"})[2], "OFFLINE")

    def test_thresholds_match_the_robot_configuration(self):
        for model, low in (("K1", remote.BATTERY_LOW["k1"]), ("T1", remote.BATTERY_LOW["t1"])):
            cfg = core.read_cfg(core.find_config("robotHealthProvider.cfg", model))
            self.assertEqual(int(cfg["batteryLow"]), low)

    def test_scan_analysis(self):
        data = robots.parse_scan("""@host Booster
@info Booster K1;firmware 1.8.0;
@bhuman no no no
@ort
@uenabled pulseaudio.service
@uenabled teamx-autostart.service
@urunning pipewire-pulse.service
@urunning teamx-vision.service
@cron @reboot /home/booster/run.sh
@booster ros.camera true
@booster lui false
@booster motion true
@proc 55.0 booster python3
@proc 30.0 booster bhuman
@proc 1.0 booster idle_thing
@docker ros2 image:latest
@end
""")
        s = robots.analyse_scan(data)
        self.assertEqual(s["foreign_units"], ["teamx-autostart.service", "teamx-vision.service"])
        self.assertEqual(s["booster_services_on"], ["ros.camera"])
        self.assertEqual(s["busy_processes"], ["python3 (booster, 55% CPU)"])
        self.assertEqual((s["installed"], s["deployed"], s["service_enabled"], s["onnxruntime"]), (False, False, False, ""))
        self.assertEqual(s["cron"], ["@reboot /home/booster/run.sh"])
        self.assertTrue(s["complete"])
        self.assertFalse(robots.analyse_scan(robots.parse_scan("@host x"))["complete"])

    def test_status_script_survives_a_log_cut_off_by_a_stop(self):
        # Seen on a robot: the newest log ended in thousands of zero bytes and no line break, so "@end" was
        # appended to that line and the dashboard showed a reachable robot as offline.
        import shutil
        import subprocess
        import tempfile
        from tests import fakerobot
        tmp = Path(tempfile.mkdtemp(prefix="bhstatus"))
        try:
            bash = fakerobot.find_bash(tmp)
            if bash is None:
                self.skipTest("no bash found that can run the repository scripts")
            (tmp / "logs").mkdir()
            (tmp / "logs" / "bhumand_1.log").write_bytes(b"started\nunexpected calib size \xe2\x80\x93 resyncing\n" + b"\0" * 4000)
            out = subprocess.run([bash, "-c", remote.STATUS_SCRIPT.replace("cd /home/booster", "cd .")], cwd=str(tmp),
                                 capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
            s = remote.parse_status(out)
            self.assertTrue(s["complete"])
            self.assertEqual(s["log"][:2], ["started", "unexpected calib size – resyncing"])
            self.assertTrue(all("\0" not in line and len(line) <= 300 for line in s["log"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class WillItRun(unittest.TestCase):
    HEALTHY = """@host k
@info Booster K1;firmware 1.7.1;
@bhuman yes yes enabled
@svcactive active
@svcenabled enabled
@svcfailed active
@statedir /var/volatile/tmp no
@statedir /run/user/1000 yes
@dir /home/booster/logs yes
@dir /home/booster/Config yes
@sudo yes
@lognew /home/booster/logs/bhumand_(3).log
@logcalib 1
@end
"""

    def checks(self, text):
        return robots.will_it_run(robots.analyse_scan(robots.parse_scan(text)))

    def test_a_healthy_robot_is_ready(self):
        checks = self.checks(self.HEALTHY)
        self.assertEqual([c for c in checks if c[0] != "ok"], [])
        self.assertEqual(robots.verdict(checks), "VERDICT: ready to run")

    def test_each_finding_is_reported(self):
        text = (self.HEALTHY.replace("firmware 1.7.1", "firmware 1.8.1.2").replace("enabled\n@svcactive", "no\n@svcactive")
                .replace("@svcenabled enabled", "@svcenabled disabled").replace("@statedir /run/user/1000 yes", "@statedir /run/user/1000 no")
                .replace("@sudo yes", "@sudo no").replace("@dir /home/booster/logs yes", "@dir /home/booster/logs no")
                .replace("@end", "@dropin /home/booster/.config/systemd/user/bhuman.service.d/controls.conf\n"
                                 "@dropinline /home/booster/.config/systemd/user/bhuman.service.d/controls.conf ExecStartPre=sudo x\n"
                                 "@campkg 1.0.2\n"
                                 "@logrep 19000 12:00:00 [cam_stream_receiver] unexpected calib size 858 (expected 490) - resyncing\n"
                                 "@logrep 6666 [cam_stream_receiver] disconnected, retrying...\n"
                                 "@logrep 900 something else repeats\n@end"))
        checks = self.checks(text)
        levels = {level: " | ".join(t for l, t in checks if l == level) for level in ("ok", "warn", "fail")}
        self.assertIn("camera stream is stuck in a loop: 19000 times", levels["fail"])
        self.assertIn("1.0.2", levels["fail"])                                   # the camera package is named
        self.assertIn("not writable", levels["fail"])
        self.assertIn("sudo", levels["fail"])
        self.assertIn("bhuman.service is disabled", levels["warn"])
        self.assertIn("no writable directory for the status file", levels["warn"])
        self.assertIn("something else repeats", levels["warn"])
        self.assertNotIn("disconnected, retrying", levels["warn"])                # part of the reported loop
        self.assertIn("has not been tested", levels["warn"])
        self.assertIn("will NOT run", robots.verdict(checks))

    def test_firmware_is_read_from_the_robot_info(self):
        self.assertEqual(robots.firmware_version(["Booster K1", "firmware 1.7.1"]), "1.7.1")
        self.assertEqual(robots.firmware_version(["Version: 1.8.1.2"]), "1.8.1.2")
        self.assertEqual(robots.firmware_version(["Booster K1"]), "")
        self.assertEqual(robots.firmware_version(["Booster K1", "Model Version: 1.0.1"]), "")   # not the firmware
        # the last entry of /opt/booster/version.txt wins over robot_info.txt
        self.assertEqual(robots.firmware_version(["firmware 1.7.1"], "1.8.1.2-release-02884-2026-10-15-global"), "1.8.1.2")
        self.assertEqual(robots.firmware_version([], "v1.4.1.0-release"), "1.4.1.0")

    def test_the_status_file_is_looked_for_where_the_software_writes_it(self):
        # InternalStateProvider.cpp, DeployDialog.cpp and bh status must agree on the places.
        for source in ("Src/Modules/Infrastructure/RobotHealthProvider/InternalStateProvider.cpp",
                       "Src/Apps/DeployDialog/DeployDialog.cpp"):
            text = (REPO / source).read_text()
            for part in ("/var/volatile/tmp", "/run/user/", "internalState.txt"):
                self.assertIn(part, text, (part, source))
        for part in ("/var/volatile/tmp/internalState.txt", "/run/user/*/internalState.txt"):
            self.assertIn(part, remote.STATUS_SCRIPT)


class LEDReport(unittest.TestCase):
    """The robot software reports its back LED (InternalStateProvider) and bh dash shows it."""

    def test_the_report_is_read_where_and_how_the_software_writes_it(self):
        src = (REPO / "Src/Modules/Infrastructure/RobotHealthProvider/InternalStateProvider.cpp").read_text()
        self.assertIn('"/ledState.txt"', src)
        order = re.findall(r"leds\.ledStates\[LEDRequest::(\w+)\]", src)
        self.assertEqual(order, ["red", "green", "blue"])
        for part in ("/var/volatile/tmp/ledState.txt", "/run/user/*/ledState.txt"):
            self.assertIn(part, remote.STATUS_SCRIPT)

    def test_state_numbers_match_the_enum(self):
        header = (REPO / "Src/Representations/Infrastructure/LEDRequest.h").read_text()
        block = re.search(r"ENUM\(State,\s*\{,(.*?)\}\);", header, re.S).group(1)
        self.assertEqual(tuple(re.findall(r"(\w+),", block)), remote.LED_STATES)
        leds = re.search(r"ENUM\(LED,\s*\{,(.*?)\}\);", header, re.S).group(1)
        self.assertEqual(re.findall(r"(\w+),", leds), ["red", "green", "blue"])

    def test_parse(self):
        self.assertEqual(remote.parse_led_report(["100", "0", "1", "2"], 112),
                         {"red": "off", "green": "on", "blue": "blinking", "age_s": 12})
        for bad in ([], ["100", "0", "1"], ["100", "0", "1", "7"], ["x", "0", "1", "2"]):
            self.assertIsNone(remote.parse_led_report(bad, 112), bad)
        status = remote.parse_status("@now 1000\n@ledstate 990 1 1 0\n@claim Dana\t5000\t900\tkick test\n@end\n")
        self.assertEqual(status["led_report"]["age_s"], 10)
        self.assertEqual((status["claim"]["who"], status["claim"]["note"], status["claim"]["age_s"]), ("Dana", "kick test", 100))
        self.assertIsNone(remote.parse_status("@claim \n@end\n")["claim"])

    def test_every_colour_is_explained_with_the_legend(self):
        from bhtool import dash
        names = [name for name, _ in dash.LED_LEGEND]
        for levels, colour in dash.LED_COLOURS.items():
            if colour == "blue":                   # only shown blinking (limp) or as a short flash
                continue
            self.assertIn(colour, names)
            states = ["off", "half", "on"]
            report = dict(zip(("red", "green", "blue"), (states[v] for v in levels)), age_s=0)
            self.assertEqual(dash.describe_led(report)["meaning"], dash.legend(colour), colour)
        limp = dash.describe_led({"red": "off", "green": "off", "blue": "blinking"})
        self.assertEqual((limp["meaning"], limp["blink"], limp["rgb_off"]), (dash.legend("blue, blinking"), "slow", [0, 0, 0]))
        busy = dash.describe_led({"red": "fastBlinking", "green": "halfFastBlinking", "blue": "off"})
        self.assertEqual(busy["meaning"], dash.legend("any colour, fast blinking"))
        remote_control = dash.describe_led({"red": "off", "green": "blinking", "blue": "off"})
        self.assertIn(dash.legend("any colour, slow blinking"), remote_control["meaning"])
        self.assertIn("playing", remote_control["meaning"])
        self.assertIn("does not show on its own", dash.describe_led({"red": "off", "green": "off", "blue": "on"})["meaning"])
        self.assertIsNone(dash.describe_led(None))


class Sharing(unittest.TestCase):
    def test_claims(self):
        from bhtool import share
        claim = share.parse_claim("Dana\t2000\t1000\tintercept test", 1500)
        self.assertEqual((claim["who"], claim["expired"], claim["age_s"], claim["left_s"]), ("Dana", False, 500, 500))
        self.assertTrue(share.parse_claim("Dana\t2000\t1000\t", 2001)["expired"])
        for bad in ("", "Dana", "\t2000\t1000", "Dana\tsoon\t1000"):
            self.assertIsNone(share.parse_claim(bad, 1500), bad)
        self.assertEqual(share.clean("Dana's \"build\"; rm -rf /\n", 80), "Danas build rm -rf")
        self.assertEqual(share.clean("x" * 100, 40), "x" * 40)

    def test_names_that_reach_the_robot_or_the_disk_are_plain(self):
        from bhtool import dash, share
        for good in ("20261117-140233",):
            self.assertTrue(share.BACKUP_ID.match(good))
        for bad in ("../x", "20261117-140233; rm", "latest"):
            self.assertIsNone(share.BACKUP_ID.match(bad))
        self.assertTrue(dash.UPLOAD_NAME.match("K1_walk-v2.onnx"))
        for bad in ("../K1.onnx", "a/b.onnx", "x.onnx.exe", ".onnx", "a b.onnx"):
            self.assertIsNone(dash.UPLOAD_NAME.match(bad), bad)


class Guard(unittest.TestCase):
    def test_guard_can_be_armed_in_every_shipped_thread_configuration(self):
        files = sorted((REPO / "Config" / "Scenarios").glob("*/*/threads.cfg"))
        self.assertGreaterEqual(len(files), 4)
        for f in files:
            text = core.read_exact(f)
            armed = deploy.guard_threads_cfg(text)
            self.assertEqual(len(armed.splitlines()), len(text.splitlines()) - 1, f)
            self.assertNotIn("//{representation = EmergencyButtonState", armed)
            self.assertRegex(armed, r"\n\s*\{representation = EmergencyButtonState; provider = EmergencyButtonProvider;\},")
            self.assertNotRegex(armed, r"(?m)^\s*EmergencyButtonState,\s*$")
            core.parse_cfg(armed)
            with self.assertRaises(core.BhError):
                deploy.guard_threads_cfg(armed)            # already armed: refuse instead of guessing

    def test_robot_listens_where_the_guard_sends(self):
        from bhtool import guard
        src = (REPO / "Src/Modules/Infrastructure/EmergencyButtonProvider/EmergencyButtonProvider.cpp").read_text()
        cfg = core.read_cfg(core.find_config("emergencyButtonProvider.cfg", "k1"))
        self.assertEqual(int(cfg["udpPort"]), guard.PORT)
        # Several heartbeats must fit into the time after which the robot stops on its own.
        self.assertGreaterEqual(int(cfg["maxWaitTimeMillis"]), 4 * guard.INTERVAL * 1000)
        self.assertIn('"noemergency"', src)
        self.assertIn('"emergency"', src)
        self.assertEqual((guard.RELEASE, guard.STOP), (b"noemergency\x00", b"emergency\x00"))


class Simulation(unittest.TestCase):
    def test_eval_script_is_passed_the_way_simulator_and_runner_read_it(self):
        import tempfile
        from bhtool import sim
        scenes = REPO / "Config" / "Scenes"
        self.assertEqual(sim.eval_script("Includes/Fast.con"), "Includes/Fast.con")
        self.assertEqual(sim.eval_script(str(scenes / "Includes" / "Fast.con")), "Includes/Fast.con")
        for bad in ("Includes/NoSuchScript.con", str(scenes / "K1" / "GameFast.ros3")):
            with self.assertRaises(core.BhError):
                sim.eval_script(bad)
        with tempfile.TemporaryDirectory() as tmp:               # exists, but not where the simulator looks
            outside = Path(tmp) / "x.con"
            outside.write_text("robot all\n")
            with self.assertRaises(core.BhError):
                sim.eval_script(str(outside))
        with self.assertRaises(core.BhError):
            sim.eval_command(0)
        self.assertEqual(sim.eval_command(4), "test game 4")
        command = sim.eval_command(8, "Includes/Fast.con")
        # The runner's parser must hand the command to the simulator unchanged ...
        sys.path.insert(0, str(REPO / "Util" / "TestRunner"))
        try:
            import cmd_parser
        finally:
            sys.path.pop(0)
        parsed = cmd_parser.parse_command(command)
        self.assertEqual((parsed["mode"], parsed["numRuns"], parsed["conFile"]), ("game", 8, "Includes/Fast.con"))
        self.assertEqual(cmd_parser.unparse_command(parsed), command)
        # ... and the simulator must still read it as "con <file.con>" relative to Config/Scenes.
        src = (REPO / "Src/Libs/SimulatedRobot/TestCommandParser.cpp").read_text()
        self.assertIn('expectToken("con")', src)
        self.assertIn('token.ends_with(".con")', src)
        self.assertIn('"/Config/Scenes/" + token', src)

    def test_variant_scripts_address_the_robots_the_scenes_define(self):
        import tempfile
        from bhtool import sim
        # The robots of each team must be the ones the scene's include file names.
        for con in sorted((REPO / "Config" / "Scenes" / "K1").glob("Game*.con")):
            include = re.search(r"call \.\./Includes/(\d+vs\d+)", con.read_text())
            if not include:
                continue
            teams = re.findall(r"^robot (robot\d.*)$",
                               (REPO / "Config/Scenes/Includes" / (include.group(1) + ".con")).read_text(), re.M)
            self.assertEqual([sim.team_robots(con.stem, False), sim.team_robots(con.stem, True)],
                             [t.split() for t in teams[:2]], con.name)
        self.assertEqual(sim.flatten_cfg("a = 1; // note\nb = {x = 2;\n  y = 3;}; /* more\n */ c = [1, 2];\n"),
                         "a = 1; b = {x = 2; y = 3;}; c = [1, 2];")
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "walkingEngine.cfg"
            cfg.write_text("speed = 5; // fast\n")
            variants = sim.parse_variants(["WalkingEngine=%s" % cfg])
            self.assertEqual(sim.variant_script(variants, ["robot21", "robot22"]),
                             "robot robot21 robot22\nset module:WalkingEngine speed = 5;\nrobot all\n")
            self.assertEqual(sim.parse_variants(["behavior:PlayBall=%s" % cfg])[0][0], "behavior:PlayBall")
            for bad in ("WalkingEngine", "Walking Engine=%s" % cfg, ":x=%s" % cfg, "WalkingEngine=%s" % (Path(tmp) / "none.cfg")):
                with self.assertRaises(core.BhError):
                    sim.parse_variants([bad])

    def test_recordings_are_read_the_way_the_simulator_writes_them(self):
        import tempfile
        from bhtool import stats
        src = (REPO / "Src/Libs/SimulatedRobot/GameController.cpp").read_text()
        for text in ('"BH_STATS_FILE"', '"BH_STATS_PERIOD"', '"t,run,state,setPlay,half,scoreA,scoreB,ballX,ballY,ballZ"',
                     ",r%dx,r%dy,r%drot,r%dup,r%dpen"):
            self.assertIn(text, src)
        self.assertIn("STATE_PLAYING  3", (REPO / "Util/GameController/include/RoboCupGameControlData.h").read_text())
        self.assertEqual(stats.STATE_PLAYING, 3)
        # Robot 0 (first team) walks, falls at 40.0 s and is up again 3 s later; the ball is kicked at 30.0 s.
        header = "t,run,state,setPlay,half,scoreA,scoreB,ballX,ballY,ballZ,r0x,r0y,r0rot,r0up,r0pen,r20x,r20y,r20rot,r20up,r20pen"
        rows = []
        for k in range(0, 800):                                   # 80 s in steps of 100 ms
            t = 100000 + k * 100
            seconds = k / 10.0
            robot_x = min(-3000 + 1000.0 * seconds, 0.0)          # 1 m/s until it reaches the ball at x = 0
            ball_x = 0.0 if seconds < 30 else 3000.0 * (seconds - 30) if seconds < 30.5 else 1500.0 + 100.0 * (seconds - 30.5)
            up = 0 if 40.0 <= seconds < 43.0 else 1
            rows.append("%d,0,3,0,1,0,%d,%d,0,50,%d,0,0,%d,0,4000,2000,3.14,1,0" % (
                t, 1 if seconds >= 70 else 0, ball_x, robot_x, up))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rec_1.csv"
            path.write_text(header + "\n" + "\n".join(rows) + "\n1000")           # the last line is cut off
            games = stats.load(str(path))
            self.assertEqual(len(games), 1)
            first, second = stats.analyze(games[0])
        self.assertEqual(first["falls"], 1)                                        # one robot, one fall
        self.assertAlmostEqual(first["getup s"], 3.0, delta=0.15)
        self.assertGreater(first["fallen %"], 3.0)
        self.assertEqual(second["falls"], 0)
        self.assertEqual(second["goals"], 1)
        self.assertEqual(first["kicks"], 1)
        self.assertGreater(first["kick speeds"][0], 1400)
        self.assertAlmostEqual(max(first["speeds"]), 1000, delta=60)               # it walked at 1 m/s for 3 s
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad_1.csv"
            bad.write_text("time,value\n1,2\n")
            with self.assertRaises(core.BhError):
                stats.load(str(bad))

    def test_walk_limits_come_from_the_file_the_scenario_selects(self):
        for scenario in ("Default", "CompetitionWalk"):
            limits = deploy.walk_limits("k1", "Default", scenario, "Default")
            self.assertIsNotNone(limits)
            text = limits[3].read_text()
            self.assertEqual(limits[3], core.find_config("walkingEngine.cfg", "k1", "Default", scenario, "Default"))
            self.assertIn("translation = {x = %d;" % limits[0], text)
        default, competition = (deploy.walk_limits("k1", "Default", s, "Default") for s in ("Default", "CompetitionWalk"))
        self.assertLess(default[0], competition[0])                # the reason for the warning on deployment
        self.assertIn("mm/s forward", deploy.describe_walk_limits("t1", "Default", "Default", "Default"))

    def test_scene_copies_give_each_team_its_scenario(self):
        from bhtool import sim
        # RoboCupCtrl.cpp gives the first child of "scenarios" to the first team, the second to the other.
        src = (REPO / "Src/Libs/SimulatedRobot/RoboCupCtrl.cpp").read_text()
        self.assertIn('resolveObject("RoboCup.scenarios"', src)
        self.assertIn("scenarios[firstTeam ? 0 : 1]", src)
        for scene in sorted((REPO / "Config" / "Scenes" / "K1").glob("Game*.ros3")):
            text = core.read_exact(scene)
            current = sim.scene_scenarios(text)
            if len(current) != 1:
                continue
            changed = sim.scene_with_scenarios(text, "Mine", current[0])
            self.assertEqual(sim.scene_scenarios(changed), ["Mine", current[0]], scene.name)
            # Nothing else may change, and writing the original pair back restores the file's meaning.
            self.assertEqual(changed.replace('      <Compound name="Mine"/>\n', ""), text, scene.name)
        with self.assertRaises(core.BhError):
            sim.scene_with_scenarios("<Simulation/>", "A", "B")

    def test_scores_are_read_as_whole_games_and_judged_with_their_noise(self):
        from bhtool import sim
        rows = [["Game No", "Half", "Kickoff", "Score A", "Score B", "Budget A", "Budget B", "Winner"],
                ["1", "1", "Team A", "2", "2", "6167", "6198", "Draw"],
                ["1", "2", "Team B", "5", "6", "324", "322", "Team B"],
                ["2", "1", "Team A", "0", "7", "6178", "6170", "Team B"],
                ["2", "2", "Team B", "3", "8", "315", "314", "Team B"]]
        self.assertEqual(sim.final_scores(rows), [(5, 6), (3, 8)])     # the second half's row is the final score
        self.assertIn("BETTER", sim.judge([3, 4, 2, 5, 3, 4])[2])
        self.assertIn("WORSE", sim.judge([-3, -4, -2, -5, -3, -4])[2])
        mean, error, verdict = sim.judge([4, -3, 6, -6, 2, -2])
        self.assertAlmostEqual(mean, 1 / 6)
        self.assertGreater(error, 1)
        self.assertIn("no clear difference", verdict)
        self.assertIn("too few", sim.judge([9])[2])


class Limits(unittest.TestCase):
    def test_rolling_distance_is_v_squared_over_twice_the_deceleration(self):
        from bhtool import limits
        self.assertAlmostEqual(limits.rolling_distance(3400, 0.3), 19267, delta=1)
        self.assertAlmostEqual(limits.rolling_distance(500, 0.3), 417, delta=1)
        self.assertIsNone(limits.rolling_distance(500, None))
        self.assertEqual(limits.number("120deg"), 120.0)
        self.assertIsNone(limits.number("none"))

    def test_the_tables_are_read_from_the_configuration(self):
        from bhtool import limits
        header, rows = limits.walk_rows("k1")
        self.assertGreater(len(header), 2)                       # one column per copy of walkingEngine.cfg
        fast = next(r for r in rows if r[0] == "fast forward")
        self.assertTrue(all(c != "-" for c in fast[1:]), fast)
        _, kicks = limits.kick_table("k1")
        self.assertGreater(len(kicks), 3)
        self.assertGreater(limits.friction("k1"), 0)
        _, getup = limits.getup_rows("k1")
        self.assertTrue(any(r[0].startswith("slow policy") and r[1] != "-" for r in getup), getup)

    def test_the_command_prints_without_error(self):
        import argparse
        import contextlib
        import io
        from bhtool import limits
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(limits.cmd_limits(argparse.Namespace(model="k1")), 0)
        for word in ("== Walk", "== Kicks", "== Getting up", "rolls"):
            self.assertIn(word, out.getvalue())

    def test_the_simulator_does_not_use_the_fast_get_up_the_robot_uses(self):
        # Docs/motion-requirements.md says so; if this fails, the document and its caveat must change.
        text = (REPO / "Src/Modules/MotionControl/GetUpEngine/GetUpEngine.cpp").read_text()
        self.assertRegex(text, r"simulatedRobot\)\s*fastActive = false;")


class Cli(unittest.TestCase):
    def test_every_command_has_help(self):
        parser = cli.build_parser()
        sub = next(a for a in parser._actions if a.dest == "command")
        for name in ("doctor", "setup", "deploy", "match", "build", "sim", "connect", "eval", "robot", "status", "stop",
                     "estop", "start", "restart", "logs", "ssh", "guard", "policy", "calib", "gc", "dash", "test"):
            self.assertIn(name, sub.choices)
        for name, p in sub.choices.items():
            p.format_help()


if __name__ == "__main__":
    unittest.main()
