"""bh dash: the robots, the game and the common jobs in one browser page.

What it shows, and where the data comes from:
  * per robot: the back LED as the robot software reports it, software state, battery, temperatures,
    wifi, disk, who is using the robot, warnings with an explanation, last log lines
                                                        <- one small ssh poll every few seconds
  * the game: state, score, time, penalties             <- GameController packets
  * with --gc: where each robot thinks it and the ball are, fallen or not
                                                        <- robots' replies to the test GameController
What it can do (each is the same code as the command line, shown live in the page):
  deploy, send policies, try a policy in the simulator, mark a robot as in use, back up and restore
  a robot, scan it, fetch its logs, stop/start it, run the laptop guard per robot, drive the test
  GameController. Anything that makes a robot go limp asks first.

It serves on 127.0.0.1 only and every request needs the token from the start URL.
Per-joint angles, camera images and the behaviour's decisions are in SimRobot: ``bh connect <robot>``.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import signal
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs

from . import core, gamecontroller as gcmod, guard as guardmod, policy, remote, share
from .core import BhError, Target, say

# Must say the same as Src/Modules/BehaviorControl/LEDHandler/LEDHandler.cpp and Docs/leds.md.
LED_LEGEND = [
    ["blue, steady", "on the robot: motors not handed over yet (after start or F1) - hold F1 2 s"],
    ["blue, blinking", "limp (unstiff) - hold STAND 2 s"],
    ["magenta", "stiff, ready pose - hold WALK 2 s"],
    ["white", "initial / finished"],
    ["white, blinking", "match mode only: initial / finished and NO GameController heard"],
    ["light blue", "ready (walking to its position)"],
    ["yellow", "set (waiting for kick-off)"],
    ["green", "playing"],
    ["red", "penalized"],
    ["orange", "calibration mode"],
    ["short blue flash", "test mode only: playing, but the ball was not seen for 3 s"],
    ["short red flash", "test mode only: battery low or a joint very hot (dark flash if the LED is red or orange)"],
    ["any colour, slow blinking", "remote control (gamepad or Drive dialog) is active"],
    ["any colour, fast blinking", "cannot do what was requested yet (e.g. still standing up)"],
]

# The colours LEDHandler.cpp sets, by brightness of red, green, blue (0 off, 1 half, 2 on).
LED_COLOURS = {
    (0, 0, 2): "blue", (2, 0, 2): "magenta", (2, 2, 2): "white", (0, 1, 2): "light blue",
    (2, 2, 0): "yellow", (0, 2, 0): "green", (2, 0, 0): "red", (2, 1, 0): "orange",
}
_LEVEL = {"off": 0, "on": 2, "blinking": 2, "fastBlinking": 2, "half": 1, "halfBlinking": 1, "halfFastBlinking": 1}
_BLINK = {"blinking": "slow", "halfBlinking": "slow", "fastBlinking": "fast", "halfFastBlinking": "fast"}
_CSS = ({0: 0, 1: 128, 2: 255}, {0: 0, 1: 140, 2: 255}, {0: 0, 1: 128, 2: 255})

UPLOAD_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}\.onnx$")
UPLOAD_MAX = 200 * 1024 * 1024
JOB_LINES = 4000            # lines kept per job
JOBS_KEPT = 30


def legend(key: Optional[str]) -> Optional[str]:
    return next((meaning for name, meaning in LED_LEGEND if name == key), None)


def describe_led(report: Optional[dict]) -> Optional[dict]:
    """How the back LED looks and what it means, from the state the robot software reported
    (see remote.parse_led_report). None without a report."""
    if not report:
        return None
    states = [report.get(c, "off") for c in ("red", "green", "blue")]
    levels = tuple(_LEVEL.get(s, 0) for s in states)
    blinks = {_BLINK[s] for s in states if s in _BLINK}
    blink = "fast" if "fast" in blinks else "slow" if blinks else "steady"
    colour = LED_COLOURS.get(levels)
    if not any(levels):
        meaning = "dark: the software switched the LED off"
    elif blink == "fast":
        meaning = legend("any colour, fast blinking")
    elif blink == "slow" and colour == "blue":
        meaning = legend("blue, blinking")
    elif blink == "slow" and colour == "white":
        meaning = legend("white, blinking") + " - or " + legend("any colour, slow blinking")
    elif blink == "slow":
        meaning = legend("any colour, slow blinking") + (" (%s: %s)" % (colour, legend(colour)) if legend(colour) else "")
    else:
        meaning = legend(colour) or "a colour this code does not show on its own - see Docs/leds.md"
    on = [_CSS[i][levels[i]] for i in range(3)]
    off = [0 if states[i] in _BLINK else on[i] for i in range(3)]
    return {"colour": colour or "mixed", "blink": blink, "meaning": meaning, "rgb": on, "rgb_off": off,
            "age_s": report.get("age_s"), "states": states}


# --------------------------------------------------------------------------
# Jobs: commands that run in the background and show their output in the page
# --------------------------------------------------------------------------

class Job:
    def __init__(self, jid: int, title: str, robots: Sequence[str], uses_config: bool):
        self.id = jid
        self.title = title
        self.robots = list(robots)
        self.uses_config = uses_config
        self.lines: List[str] = []
        self.dropped = 0
        self.state = "running"
        self.started = time.time()
        self.ended: Optional[float] = None
        self.lock = threading.Lock()

    def add(self, line: str) -> None:
        with self.lock:
            self.lines.append(line[:2000])
            if len(self.lines) > JOB_LINES:
                cut = len(self.lines) - JOB_LINES
                del self.lines[:cut]
                self.dropped += cut

    def summary(self) -> dict:
        return {"id": self.id, "title": self.title, "robots": self.robots, "state": self.state,
                "started": self.started, "ended": self.ended, "lines": self.dropped + len(self.lines)}

    def output(self, since: int) -> dict:
        with self.lock:
            start = max(0, since - self.dropped)
            return dict(self.summary(), first=self.dropped + start, text=self.lines[start:])


def run_cli(argv: Sequence[str]) -> int:
    """Run a bh command in this thread (its output goes to this thread's output sink)."""
    from . import cli
    try:
        args = cli.build_parser().parse_args(list(argv))
    except SystemExit:
        raise BhError("invalid options: bh %s" % " ".join(argv))
    say("$ bh " + " ".join(a if re.match(r"^[A-Za-z0-9_.:=/@+-]+$", a) else repr(a) for a in argv))
    return int(args.func(args) or 0)


class Dashboard:
    def __init__(self, targets: List[Target], interval: float, passive: bool,
                 test_gc: Optional[gcmod.TestGameController], guard: Optional[guardmod.Guard],
                 practice=None):
        self.practice = practice                         # practice.PracticeLab with --practice
        self.targets = targets
        self.interval = interval
        self.passive = passive
        self.test_gc = test_gc
        self.guard = guard                               # --guard: one heartbeat for all robots
        self.robot_guards: Dict[str, guardmod.Guard] = {}  # heartbeats started from the page, per robot
        self.drivers: Dict[str, guardmod.Driver] = {}       # keyboard driving from the Drive dialog, per robot
        self.token = secrets.token_urlsafe(16)
        self.me = share.me()
        self.lock = threading.Lock()
        self.status: Dict[str, dict] = {self.name(t): {"name": self.name(t), "ip": t.ip, "reachable": None,
                                                       "model": t.robot.model if t.robot else ""}
                                        for t in targets}
        self.messages: List[str] = []
        self.jobs: Dict[int, Job] = {}
        self.next_job = 1
        self.last_ui_contact = 0.0
        self.running = True
        self.poll_now = threading.Event()
        self.listener_error = None
        if practice is not None:                         # the simulator's game, never the network's
            self.listener = practice.bridge
            return
        try:
            self.listener: Optional[gcmod.Listener] = gcmod.Listener()
        except BhError as e:
            self.listener = None
            self.listener_error = str(e)

    @staticmethod
    def name(t: Target) -> str:
        return t.robot.name if t.robot else t.ip

    # -- background work -------------------------------------------------
    def all_guards(self) -> List[guardmod.Guard]:
        with self.lock:
            return ([self.guard] if self.guard else []) + list(self.robot_guards.values())

    def dead_man(self) -> None:
        """A guard only stays released while a browser tab keeps asking."""
        if time.time() - self.last_ui_contact <= 2.5:
            return
        for g in self.all_guards():
            if g.released:
                g.set_released(False)
                self.log("guard: browser stopped responding -> STOP")

    def poll_loop(self) -> None:
        while self.running:
            if not self.passive:
                for s in remote.collect_all(self.targets):
                    with self.lock:
                        self.status[s["name"]] = s
            self.dead_man()
            end = time.time() + self.interval
            while self.running and time.time() < end and not self.poll_now.is_set():
                self.dead_man()
                time.sleep(0.25)
            self.poll_now.clear()

    def log(self, text: str) -> None:
        with self.lock:
            self.messages.append("%s %s" % (time.strftime("%H:%M:%S"), text))
            del self.messages[:-8]

    # -- state for the page ----------------------------------------------
    def state(self) -> dict:
        self.last_ui_contact = time.time()
        own_team = gcmod.own_team_number()
        if self.practice is not None:                    # the practice robots are the simulator's first team
            own_team = self.practice.bridge.first_team() or own_team
        game = None
        g = None
        if self.listener:
            g, source, age, problem = self.listener.snapshot()
            if g is not None:
                game = {"source": source, "age": round(age, 1), "state": gcmod._name(gcmod.STATES, g.state),
                        "stopped": bool(g.stopped), "first_half": bool(g.first_half), "secs": g.secs_remaining,
                        "secondary": g.secondary_time, "teams": [t.number for t in g.teams],
                        "score": [t.score for t in g.teams], "set_play": gcmod._name(gcmod.SET_PLAYS, g.set_play),
                        "kicking_team": g.kicking_team, "own_in_game": g.team(own_team) is not None,
                        "players": g.players_per_team}
            elif problem:
                game = {"problem": problem}
        elif self.listener_error:
            game = {"problem": self.listener_error}
        replies = self.test_gc.reply_snapshot() if self.test_gc else {}
        robots = []
        with self.lock:
            statuses = [dict(s) for s in self.status.values()]
            messages = list(self.messages)
            jobs = [j.summary() for j in self.jobs.values()]
            heartbeats = {n: {"released": gd.released} for n, gd in self.robot_guards.items()}
        busy = {r: j["title"] for j in jobs if j["state"] == "running" for r in j["robots"]}
        for s in statuses:
            if s.get("reachable"):
                s["assess"] = remote.assess(s)
                s["battery_level"] = next((level for level, text in s["assess"] if text.startswith("battery")), "ok")
                s["led_view"] = describe_led(s.get("led_report"))
            if s.get("claim"):
                s["claim"] = dict(s["claim"], mine=s["claim"]["who"].lower() == self.me.lower())
            s["busy"] = busy.get(s["name"])
            s["heartbeat"] = heartbeats.get(s["name"])
            player = (s.get("settings") or {}).get("playerNumber", "")
            if player.isdigit():
                n = int(player)
                own = g.team(own_team) if g is not None else None
                if own is not None and 1 <= n <= gcmod.MAX_PLAYERS:
                    p = own.players[n - 1]
                    s["gc"] = {"penalty": gcmod._name(gcmod.PENALTIES, p.penalty), "secs": p.secs_till_unpenalised}
                if n in replies:
                    when, ip, r = replies[n]
                    s["reply"] = {"age": round(time.time() - when, 1), "fallen": r.fallen == 1,
                                  "pose": [round(r.pose[0]), round(r.pose[1]), round(r.pose[2] * 57.29578)],
                                  "ball_age": round(r.ball_age, 1), "ball": [round(r.ball[0]), round(r.ball[1])]}
            robots.append(s)
        return {"time": time.time(), "team": own_team, "robots": robots, "game": game, "me": self.me,
                "testgc": self.test_gc is not None, "passive": self.passive,
                "practice": self.practice.info() if self.practice is not None else None,
                "guard": {"enabled": self.guard is not None, "released": bool(self.guard and self.guard.released)},
                "jobs": jobs, "messages": messages, "legend": LED_LEGEND}

    def meta(self) -> dict:
        """What the forms need: scenarios with their walk speed per robot, builds, policies."""
        from . import deploy
        settings = core.settings()
        location = settings.get("location", "Default")
        scenarios = core.list_names("Scenarios")
        robots = {}
        for t in self.targets:
            if t.robot is None:
                continue
            robots[t.robot.name] = {"model": t.robot.model, "walk": {
                s: deploy.describe_walk_limits(t.robot.model, t.robot.name, s, location) for s in scenarios}}
        builds = {}
        for c in core.BUILD_CONFIGS:
            b = deploy.binary_path(c)
            builds[c] = {"exists": b.is_file(), "age_s": time.time() - b.stat().st_mtime if b.is_file() else None}
        return {"scenarios": scenarios, "default_scenario": settings.get("scenario", "Default"),
                "location": location, "builds": builds, "robots": robots, "policies": policy_view(),
                "uploads": [inspect_file(p) for p in sorted(upload_dir().glob("*.onnx"))]}

    # -- jobs ------------------------------------------------------------
    def start_job(self, title: str, robots: Sequence[str], uses_config: bool, work: Callable[[], int]) -> Job:
        with self.lock:
            for j in self.jobs.values():
                if j.state != "running":
                    continue
                for r in robots:
                    if r in j.robots:
                        raise BhError("%s is busy: %s (see Activity)" % (r, j.title))
                if uses_config and j.uses_config:
                    raise BhError("wait until '%s' has finished - both use the configuration" % j.title)
            job = Job(self.next_job, title, robots, uses_config)
            self.next_job += 1
            self.jobs[job.id] = job
            for old in sorted(self.jobs)[:-JOBS_KEPT]:
                if self.jobs[old].state != "running":
                    del self.jobs[old]

        def run() -> None:
            core.capture_output(job.add)
            try:
                rc = work()
            except BhError as e:
                job.add("error: %s" % e)
                rc = 1
            except Exception as e:     # a bug must end the job visibly, not leave it "running"
                job.add("unexpected error: %r" % e)
                rc = 1
            finally:
                core.capture_output(None)
            job.state = "ok" if rc == 0 else "failed"
            job.ended = time.time()
            self.log("%s: %s" % (title, "done" if rc == 0 else "FAILED"))
            self.poll_now.set()

        self.log("%s: started" % title)
        threading.Thread(target=run, daemon=True).start()
        return job

    # -- actions from the page -------------------------------------------
    def target(self, name: str) -> Target:
        for t in self.targets:
            if self.name(t) == name:
                return t
        raise BhError("unknown robot %r" % name)

    def service(self, verb: str, targets: List[Target]) -> None:
        """One thread per robot: a stop must not wait for another robot, and a start on a K1 takes ~30 s."""
        def work(t: Target) -> None:
            r = remote.run_service(t, verb)
            self.log("%s %s: %s" % (verb, t.label, "done" if r.ok else core.explain_ssh_failure(t, r)))
            self.poll_now.set()
        for t in targets:
            threading.Thread(target=work, args=(t,), daemon=True).start()

    def robot_guard(self, name: str, create: bool) -> Optional[guardmod.Guard]:
        with self.lock:
            g = self.robot_guards.get(name)
        if g is None and create:
            g = guardmod.Guard(guardmod.guard_ips([name]))
            g.start()
            with self.lock:
                self.robot_guards[name] = g
            self.log("guard for %s: heartbeat started (STOP)" % name)
        return g

    def end_drive(self, name: str) -> None:
        with self.lock:
            d = self.drivers.pop(name, None)
        if d:
            d.close()
            self.log("drive %s: ended" % name)

    def drive(self, name: str, body: dict) -> str:
        """The Drive dialog's keys, repeated by the page about ten times per second while it is open."""
        if self.practice is not None:
            raise BhError("keyboard driving needs a real robot deployed with the laptop guard; "
                          "in practice use the gamepad help or the simulator")
        t = self.target(name)
        if not body.get("on"):
            self.end_drive(name)
            return "drive ended"
        if not (self.status.get(name) or {}).get("guard"):
            raise BhError("%s was not deployed with the laptop guard - Deploy with 'Arm the laptop guard' first; "
                          "only such a robot can be driven from here" % name)
        try:
            forward, sideways, rotation = (float(body.get(k) or 0) for k in ("forward", "sideways", "rotation"))
        except (TypeError, ValueError):
            raise BhError("drive: forward, sideways and rotation must be numbers")
        with self.lock:
            d = self.drivers.get(name)
        if d is None:
            d = guardmod.Driver(guardmod.guard_ips([t.robot.name if t.robot else t.ip]))
            d.start()
            with self.lock:
                self.drivers[name] = d
            self.log("drive %s: started" % name)
        d.set(forward, sideways, rotation)
        return "ok"

    ROBOT_CONTACT = {"stop", "start", "restart", "stop_all", "deploy", "policy_push", "claim", "release", "backup",
                     "restore", "list_backups", "scan", "logs", "guard_heartbeat", "drive"}

    def action(self, body: dict) -> Tuple[str, object]:
        kind = str(body.get("action") or "")
        if self.passive and kind in self.ROBOT_CONTACT:
            raise BhError("this dashboard is passive (--passive) and does not contact the robots")
        robot = str(body.get("robot") or "")
        flag = lambda key: bool(body.get(key))
        if kind in ("stop", "start", "restart"):
            self.service(kind, [self.target(robot)])
            return "%s requested" % kind, None
        if kind == "drive":
            return self.drive(robot, body), None
        if kind == "stop_all":
            for name in list(self.drivers):
                self.end_drive(name)
            for g in self.all_guards():
                g.set_released(False)
            if self.test_gc:
                self.test_gc.set_stopped(True)
            self.service("stop", list(self.targets))
            return "HARD STOP sent to all robots", None
        if kind == "guard":
            if robot:
                self.target(robot)
                g = self.robot_guard(robot, create=flag("released"))
                if g is None:
                    return "no heartbeat for %s" % robot, None
            elif self.guard:
                g = self.guard
            else:
                raise BhError("the dashboard was started without --guard")
            g.set_released(flag("released"))
            self.log("guard%s: %s" % (" for " + robot if robot else "", "RUN" if g.released else "STOP"))
            return "guard updated", None
        if kind == "guard_heartbeat":
            self.target(robot)
            if flag("on"):
                self.robot_guard(robot, create=True)
                return "heartbeat for %s started - the robot stands still until RUN" % robot, None
            with self.lock:
                g = self.robot_guards.pop(robot, None)
            if g:
                g.close()
                self.log("guard for %s: heartbeat ended (robot stays stopped)" % robot)
            return "heartbeat ended", None
        if kind.startswith("gc_"):
            return self.gc_action(kind, body), None
        if kind == "practice_stand":
            if self.practice is None:
                raise BhError("only in practice (bh dash --practice): on a real robot hold its STAND button for 2 s")
            self.target(robot)
            message = self.practice.press_stand(robot)
            self.log(message)
            return message, None
        if kind == "deploy":
            t = self.target(robot)
            argv = ["deploy", robot]
            player = body.get("player")
            if player not in (None, "", 0):
                argv[1] += ":%d" % int(player)
            config = str(body.get("config") or "Develop")
            if config not in core.BUILD_CONFIGS:
                raise BhError("unknown build configuration %r" % config)
            argv += ["-c", config]
            scenario = body.get("scenario")
            if scenario:
                if scenario not in core.list_names("Scenarios"):
                    raise BhError("unknown scenario %r" % scenario)
                argv += ["--scenario", str(scenario)]
            if body.get("led") in ("auto", "match", "test"):
                argv += ["--led", str(body["led"])]
            for key, option in (("guard", "--guard"), ("backup", "--backup"), ("dry_run", "--dry-run"),
                                ("ignore_claim", "--ignore-claim")):
                if flag(key):
                    argv.append(option)
            if not flag("build") or self.practice is not None:   # practice robots get a stand-in binary
                argv.append("--no-build")
            job = self.start_job("deploy %s" % robot, [robot], True, lambda: run_cli(argv))
            return "deploy of %s started" % t.label, {"job": job.id}
        if kind == "policy_push":
            self.target(robot)
            argv = ["policy", "push", robot, "-y"] + (["--dry-run"] if flag("dry_run") else []) + \
                   (["--ignore-claim"] if flag("ignore_claim") else [])
            job = self.start_job("%s policies to %s" % ("compare" if flag("dry_run") else "send", robot),
                                 [robot], True, lambda: run_cli(argv))
            return "started", {"job": job.id}
        if kind == "policy_use":
            slot = policy.SLOT_BY_KEY.get(str(body.get("slot")))
            model = str(body.get("model") or "")
            scope = str(body.get("scope") or "")
            if slot is None or model not in ("k1", "t1") or scope not in ("sim", "robot", "all"):
                raise BhError("choose a slot, robot type and where to use it")
            argv = ["policy", "use", slot.key, str(policy_source(str(body.get("file") or ""), slot)),
                    "--model", model, "--scope", scope]
            job = self.start_job("use %s for %s %s (%s)" % (body.get("file"), model.upper(), slot.key, scope),
                                 [], True, lambda: run_cli(argv))
            return "started", {"job": job.id}
        if kind == "sim":
            if self.practice is not None:
                raise BhError("in practice the simulator is already running")
            model = str(body.get("model") or "k1")
            if model not in ("k1", "t1"):
                raise BhError("unknown robot type")
            job = self.start_job("simulator (%s)" % model.upper(), [], False, lambda: run_cli(["sim", "--model", model]))
            return "simulator starting", {"job": job.id}
        if kind == "claim":
            self.target(robot)
            hours = float(body.get("hours") or share.CLAIM_HOURS)
            argv = ["robot", "claim", robot, "--hours", str(hours), "--note=" + share.clean(str(body.get("note") or ""), 80)]
            argv += ["--force"] if flag("force") else []
            job = self.start_job("claim %s" % robot, [robot], False, lambda: run_cli(argv))
            return "started", {"job": job.id}
        if kind == "release":
            self.target(robot)
            argv = ["robot", "release", robot] + (["--force"] if flag("force") else [])
            job = self.start_job("release %s" % robot, [robot], False, lambda: run_cli(argv))
            return "started", {"job": job.id}
        if kind == "backup":
            self.target(robot)
            argv = ["robot", "backup", robot, "--label=" + share.clean(str(body.get("label") or ""), 60)]
            job = self.start_job("back up %s" % robot, [robot], False, lambda: run_cli(argv))
            return "started", {"job": job.id}
        if kind == "restore":
            self.target(robot)
            backup = str(body.get("backup") or "")
            if backup and not share.BACKUP_ID.match(backup):
                raise BhError("not a backup id")
            argv = ["robot", "restore", robot] + ([backup] if backup else []) + ["-y"] + \
                   (["--ignore-claim"] if flag("ignore_claim") else [])
            job = self.start_job("restore %s" % robot, [robot], False, lambda: run_cli(argv))
            return "started", {"job": job.id}
        if kind == "list_backups":
            return "ok", {"backups": share.backups(self.target(robot))}
        if kind == "scan":
            self.target(robot)
            job = self.start_job("scan %s" % robot, [robot], False, lambda: run_cli(["robot", "scan", robot]))
            return "started", {"job": job.id}
        if kind == "logs":
            t = self.target(robot)
            crash = flag("crash")
            job = self.start_job("%s of %s" % ("crash dump" if crash else "log", robot), [robot], False,
                                 lambda: fetch_log(t, crash))
            return "started", {"job": job.id}
        raise BhError("unknown action %r" % kind)

    def gc_action(self, kind: str, body: dict) -> str:
        if not self.test_gc:
            raise BhError("the dashboard was started without --gc")
        if kind == "gc_state" and body.get("state") in gcmod.STATES:
            self.test_gc.set_state(body["state"])
        elif kind == "gc_stopped":
            self.test_gc.set_stopped(bool(body.get("stopped")))
        elif kind == "gc_penalize":
            self.test_gc.penalize(int(body.get("player", 0)), gcmod.PENALTY_PICK_UP if body.get("on") else 0)
        elif kind == "gc_goal":
            self.test_gc.goal(bool(body.get("own")))
        else:
            raise BhError("unknown GameController action")
        self.log("test GameController: %s" % json.dumps({k: v for k, v in body.items() if k not in ("token", "action")}))
        return "ok"

    def job_output(self, jid: int, since: int) -> dict:
        with self.lock:
            job = self.jobs.get(jid)
        if job is None:
            raise BhError("no such job")
        return job.output(since)

    def close(self) -> None:
        self.running = False
        for name in list(self.drivers):
            self.end_drive(name)
        for g in self.all_guards():
            g.close()
        if self.test_gc:
            self.test_gc.stop()
        if self.practice is not None:
            self.practice.close()                        # also closes the listener (its bridge)
        elif self.listener:
            self.listener.close()


# --------------------------------------------------------------------------
# Policies for the page
# --------------------------------------------------------------------------

def upload_dir() -> Path:
    return core.ROOT / "Build" / "Dash" / "uploads"


def _slot_sizes(slot: policy.Slot, model: str) -> List[Tuple[Optional[int], Optional[int]]]:
    sizes = []
    for cfg_path in policy.cfg_files(slot.cfg, model):
        cfg = core.read_cfg(cfg_path)
        if core.cfg_get(cfg, slot.attr) is not None:
            sizes.append((policy._int(core.cfg_get(cfg, slot.n_in)), policy._int(core.cfg_get(cfg, slot.n_out))))
    return sizes


def inspect_file(path: Path) -> dict:
    """Signature of an ONNX file and the slots it fits, per robot type."""
    view: dict = {"name": path.name, "kb": path.stat().st_size // 1024}
    try:
        info = policy.inspect_model(path)
    except BhError as e:
        view["error"] = str(e)
        return view
    view["signature"] = "%s -> %s" % (", ".join(str(t) for t in info.inputs), ", ".join(str(t) for t in info.outputs))
    fits: Dict[str, Dict[str, Optional[str]]] = {}
    for model in ("k1", "t1"):
        fits[model] = {}
        for slot in policy.SLOTS:
            sizes = _slot_sizes(slot, model)
            problems = [p for p in (policy.signature_problem(info, n_in, n_out) for n_in, n_out in sizes) if p]
            fits[model][slot.key] = problems[0] if problems else (None if sizes else "not used for this robot type")
    view["fits"] = fits
    return view


def policy_view() -> dict:
    """Per robot type and slot: the file the simulated and the real robots use, and whether it fits."""
    models = {}
    for model in ("k1", "t1"):
        slots = {s.key: {"key": s.key, "what": s.what, "subdir": s.subdir, "sim": [], "robot": [], "problems": [],
                         "size": ""} for s in policy.SLOTS}
        for f in policy.findings(model):
            entry = slots[f.slot.key]
            parts = f.cfg.relative_to(core.config_dir()).parts
            scope = "sim" if len(parts) > 1 and parts[0] == "Robots" and parts[1].startswith("Simulated") else "robot"
            if f.model_file not in entry[scope]:
                entry[scope].append(f.model_file)
            if f.problem:
                entry["problems"].append("%s: %s" % (f.cfg.relative_to(core.config_dir()).as_posix(), f.problem))
            elif f.detail:
                entry["size"] = f.detail
        models[model] = [slots[s.key] for s in policy.SLOTS]
    files = {}
    for sub in sorted({s.subdir for s in policy.SLOTS}):
        files[sub] = [p.name for p in sorted((policy.nn_dir() / sub).glob("*.onnx"))]
    return {"models": models, "files": files}


def policy_source(name: str, slot: policy.Slot) -> Path:
    """An uploaded file, or one that already is in Config/NeuralNets/<slot directory>."""
    if not UPLOAD_NAME.match(name):
        raise BhError("not a policy file name: %r" % name)
    for candidate in (upload_dir() / name, policy.nn_dir() / slot.subdir / name):
        if candidate.is_file():
            return candidate
    raise BhError("%s is neither uploaded nor in Config/NeuralNets/%s" % (name, slot.subdir))


def save_upload(name: str, read: Callable[[int], bytes], length: int) -> dict:
    if not UPLOAD_NAME.match(name):
        raise BhError("only .onnx files with plain names (letters, digits, . _ -) can be uploaded")
    if not 0 < length <= UPLOAD_MAX:
        raise BhError("file too large (or empty)")
    upload_dir().mkdir(parents=True, exist_ok=True)
    final = upload_dir() / name
    part = upload_dir() / (name + ".part")
    left = length
    with open(part, "wb") as f:
        while left > 0:
            chunk = read(min(left, 1 << 20))
            if not chunk:
                break
            f.write(chunk)
            left -= len(chunk)
    if left:
        part.unlink()
        raise BhError("upload incomplete")
    os.replace(str(part), str(final))
    view = inspect_file(final)
    if "error" in view:
        final.unlink()
        raise BhError("%s is not a usable ONNX file: %s" % (name, view["error"]))
    return view


def fetch_log(t: Target, crash: bool) -> int:
    if crash:
        script = 'cat "$HOME/bhdump.log" 2>/dev/null || echo "no crash dump on the robot"'
    else:
        script = ('f=$(ls -t "$HOME"/logs/bhumand_*.log 2>/dev/null | head -1); '
                  '[ -n "$f" ] && { echo "== $f"; tail -n 400 "$f"; } || echo "no log yet"')
    r = core.ssh_run(t.ip, script, timeout=30)
    if not r.ok:
        raise BhError(core.explain_ssh_failure(t, r))
    say(r.out.rstrip())
    return 0


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def make_handler(dash: Dashboard):
    page = (Path(__file__).with_name("dash.html")).read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, value) -> None:
            self._send(code, json.dumps(value).encode("utf-8"))

        def _allowed(self, token: Optional[str]) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost") and token is not None and secrets.compare_digest(token, dash.token)

        def _query(self) -> Tuple[str, Dict[str, str]]:
            path, _, query = self.path.partition("?")
            return path, {k: v[0] for k, v in parse_qs(query).items()}

        def do_GET(self):
            path, q = self._query()
            if not self._allowed(q.get("t")):
                self._send(403, b'{"error": "use the address printed by bh dash"}')
                return
            try:
                if path == "/":
                    self._send(200, page, "text/html; charset=utf-8")
                elif path == "/api/state":
                    self._json(200, dash.state())
                elif path == "/api/meta":
                    self._json(200, dash.meta())
                elif path == "/api/job":
                    self._json(200, dash.job_output(int(q.get("id", "0")), int(q.get("since", "0"))))
                else:
                    self._send(404, b'{"error": "not found"}')
            except (BhError, ValueError) as e:
                self._json(400, {"error": str(e)})

        def do_POST(self):
            path, q = self._query()
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._send(400, b'{"error": "bad request"}')
                return
            if path == "/api/upload":
                consumed = [0]

                def read(n: int) -> bytes:
                    data = self.rfile.read(n)
                    consumed[0] += len(data)
                    return data

                def refuse(code: int, message: str) -> None:
                    # Read what the browser is still sending: answering without it can reset the
                    # connection, and the browser would then show no reason at all.
                    left = length - consumed[0]
                    if left > UPLOAD_MAX:
                        self.close_connection = True
                    while 0 < left <= UPLOAD_MAX:
                        chunk = self.rfile.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        left -= len(chunk)
                    self._json(code, {"error": message})

                if not self._allowed(q.get("t")):
                    refuse(403, "forbidden")
                    return
                try:
                    self._json(200, {"ok": "uploaded", "data": save_upload(q.get("name", ""), read, length)})
                except BhError as e:
                    refuse(400, str(e))
                return
            try:
                body = json.loads(self.rfile.read(min(length, 65536)) or b"{}")
            except (ValueError, OSError):
                self._send(400, b'{"error": "bad request"}')
                return
            if not isinstance(body, dict) or not self._allowed(body.get("token")) or path != "/api/action":
                self._send(403, b'{"error": "forbidden"}')
                return
            try:
                message, data = dash.action(body)
                self._json(200, {"ok": message, "data": data})
            except (BhError, ValueError, TypeError) as e:
                self._json(400, {"error": str(e)})

    return Handler


def start_practice(args):
    """--practice: stand-in robots driven by SimRobot (see practice.py). Returns the lab and the targets."""
    from . import practice
    if args.robots or args.gc or args.guard or args.passive:
        raise BhError("--practice makes its own robots and GameController; leave out robot names, --gc, "
                      "--guard and --passive")
    lab = practice.PracticeLab(args.model, args.scene, args.count, args.sim_config, args.sim_port, args.headless)
    lab.find_simulator()
    say("Practice: setting up %d stand-in robot(s) in %s ..." % (lab.count, lab.base))
    names = lab.prepare()
    try:
        lab.start()
    except BaseException:
        lab.close()
        raise
    say("Practice: SimRobot plays %s (%s): %s are its first team robot%s. Nothing reaches a real robot."
        % (lab.scene, args.model.upper(), ", ".join(names), "s 1-%d" % len(names) if len(names) > 1 else " 1"))
    return lab, core.resolve_targets(names, prefer="lan")


def cmd_dash(args) -> int:
    if args.who:
        os.environ["BH_USER"] = share.clean(args.who, 40)
    port = args.port or (8090 if args.practice else 8089)
    lab = None
    if args.practice:
        lab, targets = start_practice(args)
        test_gc = lab.gc
    else:
        targets = core.resolve_targets(args.robots) if (args.robots or core.load_robots()) else []
        test_gc = gcmod.start_test_gc(args.robots, args.players, 0, False) if args.gc else None
    guard = None
    if args.guard:
        guard = guardmod.Guard(guardmod.guard_ips([t.robot.name if t.robot else t.ip for t in targets]))
        guard.start()
    dash = Dashboard(targets, args.interval, args.passive, test_gc, guard, practice=lab)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(dash))
    except OSError as e:
        dash.close()
        raise BhError("cannot open port %d (%s) - try --port" % (port, e))
    threading.Thread(target=dash.poll_loop, daemon=True).start()
    url = "http://127.0.0.1:%d/?t=%s" % (port, dash.token)
    say("Dashboard: %s" % url)
    say("Watching %d robot(s) as %s%s%s%s. Ctrl+C to quit."
        % (len(targets), dash.me, " (passive: no ssh, GameController only)" if args.passive else "",
           ", test GameController ON" if test_gc else "", ", laptop guard ON (robots stopped until RUN)" if guard else ""))
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def quit_on_signal(signum, frame):
        raise KeyboardInterrupt                          # clean up as with Ctrl+C: guards STOP, simulator ends
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), quit_on_signal)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        say()
    finally:
        server.server_close()
        dash.close()
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("dash", help="the robots, the game and the common jobs in one browser page",
                       description=__doc__, formatter_class=__import__("argparse").RawDescriptionHelpFormatter)
    p.add_argument("robots", nargs="*", metavar="robot", help="default: all registered robots")
    p.add_argument("--gc", action="store_true", help="also run the test GameController (practice only)")
    p.add_argument("--players", type=int, default=5, help="players per team for --gc (default 5)")
    p.add_argument("--guard", action="store_true",
                   help="act as the laptop guard for ALL robots from the start (the page can also start it per robot)")
    p.add_argument("--passive", action="store_true", help="never contact the robots (only listen to the GameController)")
    p.add_argument("--interval", type=float, default=4.0, help="seconds between robot polls (default 4)")
    p.add_argument("--as", dest="who", help="your name as others see it on claimed robots (default: git user name)")
    p.add_argument("--port", type=int, help="where to serve the page (default 8089, with --practice 8090)")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    q = p.add_argument_group("practice on simulated robots (nothing reaches a real robot)")
    q.add_argument("--practice", action="store_true",
                   help="stand-in robots Sim1, Sim2, ... whose bodies are SimRobot's first team")
    q.add_argument("--model", choices=["k1", "t1"], default="k1", help="robot type (default k1)")
    q.add_argument("--scene", help="SimRobot game scene (default: GameFast3v3 for K1, GameFast for T1)")
    q.add_argument("--count", type=int, default=0, help="how many practice robots (default: up to 3)")
    q.add_argument("--sim-config", choices=core.BUILD_CONFIGS, default="Develop", help="SimRobot build (default Develop)")
    q.add_argument("--sim-port", type=int, default=10990, help="UDP port of the link to SimRobot (default 10990)")
    q.add_argument("--headless", action="store_true", help="SimRobot without a window")
    p.set_defaults(func=cmd_dash)
