"""bh dash --practice: try the dashboard on simulated robots before using it on real ones.

  * The practice robots Sim1, Sim2, ... are stand-ins. A directory per robot plays the robot's file
    system and a copy of Config plays the checkout (the stand-ins of the tool's own tests,
    Util/bh/tests/fakerobot.py), all in Build/Practice. They are registered under loopback addresses
    (127.0.0.11, 127.0.0.12, ...), so deploy, claim, back up, restore, logs, scan and the laptop guard run
    their real code - and nothing can reach a real robot. Your checkout is not changed.
  * SimRobot plays their bodies: practice robot N is the simulated robot "robotN" of the first team.
    SimRobot is started with BH_PRACTICE_PORT (ConsoleRoboCupCtrl::updatePracticeBridge: it accepts
    only a few console commands, from this computer) and BH_PRACTICE_DIR (InternalStateProvider writes
    each simulated robot's back LED there).
  * What happens to a practice robot happens to its simulated body, as it would to a real robot:
      software stopped (Stop, STOP ALL, restore) or started (Start, Restart, deploy) -> limp, it falls;
      "Hold STAND" on its card makes it stand up (on a real robot: set it on its feet, hold STAND for 2 s);
      deployed with the guard -> it stands still unless the page sends RUN, exactly as with the real guard;
      the test GameController buttons -> the simulator's GameController.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import core, gamecontroller as gcmod, guard as guardmod
from .core import BhError, say

PORT = 10990                    # SimRobot listens here, this program on PORT + 1 (ConsoleRoboCupCtrl.cpp)
DEFAULT_SCENES = {"k1": "GameFast3v3", "t1": "GameFast"}
MAX_ROBOTS = 5
GUARD_TIMEOUT = 1.0             # like EmergencyButtonProvider: no RUN heard for this long means stop
TEAMMATE = "Kim (practice teammate)"
STANDING_HEIGHT = {"k1": 537, "t1": 670}   # mm, torso of a standing robot (Config/Scenes/*/Game*.ros3)
UNSTIFF = "dr module:GameStateProvider:unstiff"
STAND_UP = "dr module:GameStateProvider:active"


def address(n: int) -> str:
    """The loopback address of practice robot n (1-based)."""
    return "127.0.0.%d" % (10 + n)


def emergency_command(pressed: bool) -> str:
    # An explicit value: "unchanged" would leave the last value in place (EmergencyButtonProvider does
    # not write the representation in the simulator).
    return "set representation:EmergencyButtonState buttonPressed = %s;" % ("true" if pressed else "false")


class Bridge:
    """The UDP link to SimRobot. Also stands in for gamecontroller.Listener (snapshot/close): the game
    shown is the simulator's, never a GameController on the network."""

    def __init__(self, port: int, alive: Callable[[], bool] = lambda: True):
        self.port = port
        self.alive_check = alive
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.rx.bind(("127.0.0.1", port + 1))
        except OSError as e:
            self.rx.close()
            raise BhError("cannot listen on UDP %d (%s). Is another practice dashboard running? "
                          "Use --sim-port." % (port + 1, e))
        self.rx.settimeout(0.3)
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.lock = threading.Lock()
        self.last: Optional[gcmod.GameData] = None
        self.last_time = 0.0
        self.replies: Dict[int, Tuple[float, str, gcmod.ReturnData]] = {}
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while self.running:
            try:
                data, _ = self.rx.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if data[:4] == gcmod.RETURN_HEADER:
                try:
                    r = gcmod.decode_return(data)
                except ValueError:
                    continue
                with self.lock:
                    self.replies[r.player] = (time.time(), "simulator", r)
            elif gcmod.classify(data) is None:
                g = gcmod.decode(data)
                with self.lock:
                    self.last, self.last_time = g, time.time()

    def send(self, *lines: str) -> None:
        try:
            self.tx.sendto("\n".join(lines).encode("utf-8"), ("127.0.0.1", self.port))
        except OSError:
            pass

    def heard_within(self, seconds: float) -> bool:
        with self.lock:
            return self.last is not None and time.time() - self.last_time <= seconds

    def first_team(self) -> Optional[int]:
        with self.lock:
            return self.last.teams[0].number if self.last else None

    def snapshot(self) -> Tuple[Optional[gcmod.GameData], Optional[str], float, Optional[str]]:
        with self.lock:
            age = time.time() - self.last_time if self.last else float("inf")
            g = self.last
        problem = None
        if not self.alive_check():
            problem = "the simulator has ended - quit and start bh dash --practice again"
        elif g is None:
            problem = "waiting for the simulator to start"
        return g, "simulator", age, problem

    def reply_snapshot(self) -> Dict[int, Tuple[float, str, gcmod.ReturnData]]:
        with self.lock:
            return {n: v for n, v in self.replies.items() if time.time() - v[0] < 3}

    def close(self) -> None:
        self.running = False
        for s in (self.rx, self.tx):
            try:
                s.close()
            except OSError:
                pass


class GuardEar:
    """Receives what the laptop guard sends to one practice robot (UDP 9910 at its loopback address)
    and judges it like EmergencyButtonProvider does."""

    def __init__(self, ip: str):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((ip, guardmod.PORT))
        except OSError as e:
            self.sock.close()
            raise BhError("cannot listen on %s UDP %d for the practice guard (%s)" % (ip, guardmod.PORT, e))
        self.sock.settimeout(0.3)
        self.released_at = 0.0
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while self.running:
            try:
                data, _ = self.sock.recvfrom(64)
            except socket.timeout:
                continue
            except OSError:
                break
            if data == guardmod.RELEASE:
                self.released_at = time.time()
            elif data == guardmod.STOP:
                self.released_at = 0.0

    def released(self) -> bool:
        return time.time() - self.released_at <= GUARD_TIMEOUT

    def close(self) -> None:
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass


class PracticeRobot:
    def __init__(self, n: int, name: str, root: Path):
        self.n = n
        self.name = name
        self.sim = "robot%d" % n
        self.ip = address(n)
        self.root = root                       # the stand-in file system
        self.ear: Optional[GuardEar] = None
        self.service_stamp: Optional[int] = None  # when the software was last started/stopped (None: not looked yet)
        self.emergency: Optional[bool] = None  # what the simulated robot was told last
        self.limp_pending = False              # a stop/start happened while the simulator was not there

    def software_running(self) -> bool:
        try:
            return (self.root / "service.state").read_text().strip() == "active"
        except OSError:
            return False

    def service_changed(self) -> bool:
        """Whether the software was started, restarted or stopped since the last call (the stand-in
        systemctl rewrites service.state each time)."""
        try:
            stamp = (self.root / "service.state").stat().st_mtime_ns
        except OSError:
            stamp = 0
        changed = self.service_stamp is not None and stamp != self.service_stamp
        self.service_stamp = stamp
        return changed

    def guarded(self) -> bool:
        """Deployed with --guard: the robot-specific threads.cfg names EmergencyButtonProvider."""
        return (self.root / "home" / "booster" / "Config" / "Robots" / self.name / "threads.cfg").is_file()

    def player(self) -> int:
        try:
            text = (self.root / "home" / "booster" / "Config" / "settings.cfg").read_text()
        except OSError:
            return self.n
        m = re.search(r"playerNumber\s*=\s*(\d+)", text)
        return int(m.group(1)) if m else self.n


class PracticeGameController:
    """The dashboard's test GameController buttons, applied to the simulator's GameController."""

    def __init__(self, lab: "PracticeLab"):
        self.lab = lab

    def set_state(self, name: str) -> None:
        if name not in gcmod.STATES:
            raise BhError("unknown game state %r" % name)
        self.lab.bridge.send("gc " + name)

    def set_stopped(self, stopped: bool) -> None:
        self.lab.freeze = stopped              # every robot stands still, like the GameController's stop
        self.lab.sync_now.set()

    def penalize(self, player: int, penalty: int = gcmod.PENALTY_PICK_UP) -> None:
        robot = self.lab.robot_for_player(player)
        self.lab.bridge.send("robot " + robot.sim, "pr manual" if penalty else "pr none")

    def goal(self, own: bool) -> None:
        self.lab.bridge.send("gc goal " + ("firstTeam" if own else "secondTeam"))

    def reply_snapshot(self) -> Dict[int, Tuple[float, str, gcmod.ReturnData]]:
        return self.lab.bridge.reply_snapshot()

    def stop(self) -> None:
        pass


class PracticeLab:
    def __init__(self, model: str, scene: str, count: int, config: str, port: int = PORT,
                 headless: bool = False, base: Optional[Path] = None, start_simulator: bool = True):
        if model not in DEFAULT_SCENES:
            raise BhError("unknown robot type %r" % model)
        self.model = model
        self.scene = scene or DEFAULT_SCENES[model]
        self.config = config
        self.port = port
        self.headless = headless
        self.real_root = core.ROOT
        self.base = base or core.ROOT / "Build" / "Practice"
        self.start_simulator = start_simulator
        self.robots: List[PracticeRobot] = []
        self.proc: Optional[subprocess.Popen] = None
        self.bridge: Optional[Bridge] = None
        self.gc = PracticeGameController(self)
        self.freeze = False
        self.sim_ready = False
        self.running = False
        self.sync_now = threading.Event()
        self.saved: Dict[str, object] = {}
        self.scene_file: Optional[Path] = None
        self.exe: Optional[Path] = None
        size = 3 if "3v3" in self.scene else 11 if "11v11" in self.scene else 1 if self.scene.startswith("Robot") else 5
        self.count = max(1, min(count or min(size, 3), size, MAX_ROBOTS))
        if self.scene.startswith("Robot"):
            raise BhError("the one-robot scenes call their robot robot5; use a game scene (default %s)"
                          % DEFAULT_SCENES[model])

    # -- setting up ------------------------------------------------------
    def find_simulator(self) -> None:
        from . import deploy, sim
        names = sim.scenes(self.model)
        match = [n for n in names if n.lower() == self.scene.lower()]
        if not match:
            raise BhError("unknown scene %r for %s. Available: %s" % (self.scene, self.model.upper(), ", ".join(names)))
        self.scene = match[0]
        self.scene_file = sim.scene_dir(self.model) / (self.scene + ".ros3")
        if self.start_simulator:
            self.exe = deploy.simrobot_path(self.config)
            if not self.exe.exists():
                raise BhError("SimRobot (%s) is not built yet: run  bh build sim -c %s  first" % (self.config, self.config))

    def prepare(self) -> List[str]:
        """Create the stand-in robots and the practice copy of the configuration, deploy to them, and
        point this program at them. Returns the robot names."""
        try:
            from tests import fakerobot            # the stand-ins of the tool's own tests
        except ImportError:
            raise BhError("Util/bh/tests/fakerobot.py is missing - practice mode needs it")
        from . import cli, robots
        if self.base.exists():
            shutil.rmtree(self.base)
        self.base.mkdir(parents=True)
        bash = fakerobot.find_bash(self.base)
        if not bash:
            raise BhError("practice mode needs bash (on Windows: Git for Windows)")
        repo = fakerobot.make_repo(self.base)
        for c in core.BUILD_CONFIGS:
            fakerobot._write(repo / "Build" / "Linux" / "Booster" / c / "bhuman", "practice robot binary (%s)\n" % c)
        tools = fakerobot.make_local_tools(self.base)
        self.saved = {"ROOT": core.ROOT, "tcp_open": core.tcp_open, "ssh_key": core.ssh_key,
                      "env": {k: os.environ.get(k) for k in ("FAKE_ROBOTS", "FAKE_BASH", "BH_BASH", "BH_SSH", "PATH")}}
        robots_dir = self.base / "robots"
        os.environ.update({"FAKE_ROBOTS": robots_dir.as_posix(), "FAKE_BASH": bash, "BH_BASH": '"%s"' % bash,
                           "BH_SSH": '"%s" "%s"' % (sys.executable, tools / "fake_ssh.py"),
                           "PATH": str(tools) + os.pathsep + os.environ.get("PATH", "")})
        core.ROOT = repo
        core.ssh_key = lambda: repo / "Install" / "Keys" / "id_rsa_b-human"
        core.tcp_open = lambda ip, port=22, timeout=0.8: \
            (robots_dir / ip).is_dir() and not (robots_dir / ip / "offline").exists()

        lines: List[str] = []
        core.capture_output(lines.append)
        try:
            team = core.settings().get("teamNumber", "")
            for n in range(1, self.count + 1):
                name = "Sim%d" % n
                robots.add_robot(name, 10 + n, self.model, team)
                net = repo / "Config" / "Robots" / name / "network.cfg"
                text = core.read_exact(net)
                text = re.sub(r'((?:lan|wlan)\s*=\s*")[^"]*(")', lambda m: m.group(1) + address(n) + m.group(2), text)
                with open(net, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                root = fakerobot.make_robot(self.base, name, address(n))
                if self.model == "t1":
                    fakerobot._write(root / "opt" / "booster" / "robot_info.txt", "Name: Booster T1\nModel Version: 1.0.1\n")
                self.robots.append(PracticeRobot(n, name, root))
            args = cli.build_parser().parse_args(["deploy"] + ["%s:%d" % (r.name, r.n) for r in self.robots]
                                                 + ["--no-build"])
            rc = int(args.func(args) or 0)
        except BhError as e:
            lines.append("error: %s" % e)
            rc = 1
        finally:
            core.capture_output(None)
        if rc:
            self.restore()
            raise BhError("setting up the practice robots failed:\n  " + "\n  ".join(lines[-15:]))
        for r in self.robots:
            if not r.software_running():
                raise BhError("practice robot %s did not start" % r.name)
        if self.count >= 3:                     # somebody else uses one robot, as in a shared lab
            now = int(time.time())
            fakerobot._write(self.robots[2].root / "home" / "booster" / ".bh-claim",
                             "%s\t%d\t%d\twalk test, back at 16:00\n" % (TEAMMATE, now + 4 * 3600, now - 1200))
        return [r.name for r in self.robots]

    def start(self) -> None:
        """Start the simulator, the bridge, the guard ears and the loop that keeps them in step."""
        if self.start_simulator:
            self.start_sim()
        self.bridge = Bridge(self.port, alive=lambda: self.proc is None or self.proc.poll() is None)
        for r in self.robots:
            r.ear = GuardEar(r.ip)
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def start_sim(self) -> None:
        env = dict(os.environ, BH_PRACTICE_PORT=str(self.port), BH_PRACTICE_DIR=str(self.base / "sim"))
        for key in ("FAKE_ROBOTS", "FAKE_BASH", "BH_BASH", "BH_SSH"):
            env.pop(key, None)
        saved_path = self.saved.get("env", {}).get("PATH") if self.saved else None
        if saved_path is not None:
            env["PATH"] = saved_path
        cmd = [str(self.exe)] + (["-noWindow"] if self.headless else []) + [str(self.scene_file)]
        if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            if not shutil.which("xvfb-run"):
                raise BhError("no display for SimRobot. Run this in a desktop session, or install xvfb "
                              "(sudo apt install xvfb) to run it without a window.")
            cmd = ["xvfb-run", "-a"] + cmd
        log = open(self.base / "simrobot.log", "wb")
        self.proc = subprocess.Popen(cmd, cwd=str(self.real_root), env=env, stdout=log, stderr=subprocess.STDOUT)

    # -- keeping the simulation in step with the practice robots ---------
    def robot(self, name: str) -> PracticeRobot:
        for r in self.robots:
            if r.name.lower() == name.lower():
                return r
        raise BhError("unknown practice robot %r" % name)

    def robot_for_player(self, player: int) -> PracticeRobot:
        for r in self.robots:
            if r.player() == player:
                return r
        raise BhError("no practice robot has player number %d" % player)

    def press_stand(self, name: str) -> str:
        """Like a person at a real robot: hold STAND, and set the robot on its feet if it lies on the
        ground (a limp simulated robot does not get up from a heap by itself; placing it 1 s after
        STAND was upright within 1 s in 6 of 6 tries)."""
        r = self.robot(name)
        if not r.software_running():
            raise BhError("the software of %s is not running - Start it first" % r.name)
        self.bridge.send("robot " + r.sim, STAND_UP)

        def place() -> None:
            reply = self.bridge.reply_snapshot().get(r.n)
            if reply is not None and reply[2].fallen:
                x, y, rotation = reply[2].pose      # the robot's own coordinates, as "mv" takes them
                self.bridge.send("robot " + r.sim, "mv %d %d %d 0 0 %d"
                                 % (round(x), round(y), STANDING_HEIGHT[self.model], round(rotation * 57.29578)))
        timer = threading.Timer(1.0, place)
        timer.daemon = True
        timer.start()
        return "%s: STAND held for 2 s - it stands up" % r.name

    def _loop(self) -> None:
        last_led = 0.0
        while self.running:
            try:
                self.sync(copy_leds=time.time() - last_led >= 1.0)
            except Exception as e:                 # keep the loop alive; show what went wrong
                say("practice: %r" % e)
            if time.time() - last_led >= 1.0:
                last_led = time.time()
            self.sync_now.wait(0.2)
            self.sync_now.clear()

    def sync(self, copy_leds: bool = True) -> None:
        bridge = self.bridge
        if bridge is None:
            return
        alive = bridge.heard_within(2.0)
        if alive and not self.sim_ready:            # the simulator (re)appeared: set up every robot
            for r in self.robots:
                bridge.send("robot " + r.sim, "for Cognition mr EmergencyButtonState EmergencyButtonProvider")
                r.emergency = None
        self.sim_ready = alive
        for r in self.robots:
            active = r.software_running()
            if r.service_changed():
                r.limp_pending = True              # stopping and (re)starting the software leave it limp
            emergency = self.freeze or (r.guarded() and not (r.ear is not None and r.ear.released()))
            if not alive:
                continue
            if r.limp_pending:
                bridge.send("robot " + r.sim, UNSTIFF)
                r.limp_pending = False
            if emergency != r.emergency:
                bridge.send("robot " + r.sim, emergency_command(emergency))
                r.emergency = emergency
            if copy_leds and active:
                self.copy_state(r, bridge.first_team())

    def copy_state(self, r: PracticeRobot, team: Optional[int]) -> None:
        """The simulated robot's LED -> where the real one writes it (InternalStateProvider). Only the LED:
        the simulator has no battery, so the stand-in keeps its own internalState.txt."""
        if team is None:
            return
        try:
            text = (self.base / "sim" / ("%d-%d" % (team, r.n)) / "ledState.txt").read_text()
        except OSError:
            return
        if text.strip():
            target = r.root / "run" / "user" / "1000"
            target.mkdir(parents=True, exist_ok=True)
            (target / "ledState.txt").write_text(text)

    # -- the end -----------------------------------------------------------
    def close(self) -> None:
        self.running = False
        self.sync_now.set()
        for r in self.robots:
            if r.ear:
                r.ear.close()
        if self.bridge:
            self.bridge.close()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.restore()

    def restore(self) -> None:
        """Point this program back at the real checkout."""
        if not self.saved:
            return
        core.ROOT = self.saved["ROOT"]
        core.tcp_open = self.saved["tcp_open"]
        core.ssh_key = self.saved["ssh_key"]
        for k, v in self.saved["env"].items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.saved = {}

    def info(self) -> dict:
        return {"scene": "%s (%s)" % (self.scene, self.model.upper()), "robots": [r.name for r in self.robots],
                "simulator": self.sim_ready, "teammate": TEAMMATE}
