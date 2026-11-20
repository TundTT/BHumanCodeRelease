"""Build and deploy, with the safety net the plain deploy script lacks.

``Make/Common/deploy`` copies Config/ to the robot with ``rsync --del``. That
silently destroys three things that only exist on the robot:
  * a calibration that was saved on the robot but never downloaded,
  * TensorRT engines the robot compiled for its own hardware (several minutes
    of start-up time each time they are lost),
  * nothing else of value - which is exactly why the laptop guard override
    (Robots/<name>/threads.cfg) can rely on a normal deploy removing it.
``bh deploy`` rescues the first two before it calls the script, checks the
policies, verifies afterwards that the robot really runs the new binary, and
only then starts the software. ``bh match`` adds the match-day checklist.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Tuple

from . import core, policy, remote, share
from .core import BhError, Target, say

CALIBRATION_FILE = re.compile(r"^Config/Robots/([A-Za-z0-9_-]+)/(cameraCalibration|imuCalibration)\.cfg$")
CACHE_FILE = re.compile(r"^[A-Za-z0-9_.-]+\.(engine|profile)$")
PLAYER_RANGE = range(1, 21)

PROBE_SCRIPT = r'''
cd /home/booster 2>/dev/null
echo "@host $(hostname)"
echo "@newest $(ls -t Config/settings.cfg Config/Robots/*/cameraCalibration.cfg Config/Robots/*/imuCalibration.cfg 2>/dev/null | head -1)"
for f in Config/Robots/*/cameraCalibration.cfg Config/Robots/*/imuCalibration.cfg; do
  [ -f "$f" ] && echo "@calib $(sha256sum "$f" | cut -d' ' -f1) $f"
done
for f in Config/NeuralNets/TensorRTCache/*; do
  [ -f "$f" ] && echo "@trt $(stat -c %s "$f") $(basename "$f")"
done
echo "@end"
'''

VERIFY_SCRIPT = r'''
cd /home/booster 2>/dev/null
echo "@sha $(sha256sum Config/bhuman 2>/dev/null | cut -d' ' -f1)"
echo "@settings $(tr '\n' ' ' 2>/dev/null <Config/settings.cfg)"
echo "@ort $(ls Config/libonnxruntime.so.1 2>/dev/null)"
echo "@end"
'''


class Job(NamedTuple):
    target: Target
    player: Optional[int]


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------

def binary_path(config: str) -> Path:
    return core.ROOT / "Build" / "Linux" / "Booster" / config / "bhuman"


def robot_build_dir(config: str) -> Optional[Path]:
    """The CMake build tree for the robot binary. Projects generated on Windows
    keep it in a Booster-prefix subdirectory, Linux ones do not."""
    base = core.ROOT / "Build" / "Linux" / "CMake" / config
    for d in (base / "Booster-prefix", base):
        if (d / "CMakeCache.txt").is_file():
            return d
    return None


def check_hostname(target: core.Target) -> None:
    """The robot software names itself after the hostname (Src/Apps/Booster/Main.cpp) and reads
    Config/Robots/<hostname>/. Deployed under another name, the robot's own files - its calibration and the
    laptop guard's threads.cfg - are not used, and nothing else would tell."""
    if not target.robot:
        return
    r = core.ssh_run(target.ip, "hostname", timeout=10)
    host = r.out.strip()
    if r.ok and host and host != target.robot.name:
        raise BhError("%s answers with the hostname '%s', but is registered here as '%s'. The robot software "
                      "would read Config/Robots/%s/ and ignore Config/Robots/%s/ - with it the robot's calibration "
                      "and the laptop guard. Register it under its hostname (bh robot remove %s, then bh robot add "
                      "%s --id ...) or give it this name (bh robot install %s)."
                      % (target.label, host, target.robot.name, host, target.robot.name, target.robot.name, host,
                         target.robot.name))


def build_robot(config: str) -> None:
    say("== Building the robot software (%s)" % config)
    if core.is_macos():
        core.run_script("Make/macOS/compile", ["Booster", config])
    else:
        d = robot_build_dir(config)
        if d is None:
            raise BhError("the project files have not been generated yet. Run: bh generate   (see bh doctor)")
        core.run_bash("cmake --build '%s' --target Booster --config %s"
                      % (d.relative_to(core.ROOT).as_posix(), config))
    if not binary_path(config).is_file():
        raise BhError("the build finished but %s is missing" % binary_path(config))


def simrobot_path(config: str) -> Path:
    base = core.ROOT / "Build" / core.host_os() / "SimRobot" / config
    if core.is_windows():
        return base / "SimRobot.exe"
    if core.is_macos():
        return base / "SimRobot.app" / "Contents" / "MacOS" / "SimRobot"
    return base / "SimRobot"


def build_sim(config: str) -> None:
    say("== Building the simulator (%s)" % config)
    if core.is_macos():
        core.run_script("Make/macOS/compile", ["SimRobot", config])
    elif core.is_windows():
        tree = core.ROOT / "Build" / "Windows" / "CMake"
        if not (tree / "CMakeCache.txt").is_file():
            raise BhError("the project files have not been generated yet. Run: bh generate   (see bh doctor)")
        rc = subprocess.call(["cmake.exe", "--build", str(tree), "--target", "SimRobot", "--config", config])
        if rc != 0:
            raise BhError("building SimRobot failed (exit code %d)" % rc)
    else:
        tree = core.ROOT / "Build" / "Linux" / "CMake" / config
        if not (tree / "CMakeCache.txt").is_file():
            raise BhError("the project files have not been generated yet. Run: bh generate   (see bh doctor)")
        core.run_bash("cmake --build 'Build/Linux/CMake/%s' --target SimRobot --config %s" % (config, config))


def generate() -> None:
    say("== Generating project files")
    if core.is_windows():
        rc = subprocess.call(["cmd.exe", "/c", str(core.ROOT / "Make" / "Windows" / "generate.cmd")])
        if rc != 0:
            raise BhError("Make/Windows/generate.cmd failed (exit code %d)" % rc)
    elif core.is_macos():
        core.run_script("Make/macOS/generate")
    else:
        core.run_script("Make/Linux/generate")
        # The script does not report a failed CMake run through its exit code.
        missing = [c for c in core.BUILD_CONFIGS
                   if not (core.ROOT / "Build" / "Linux" / "CMake" / c / "CMakeCache.txt").is_file()]
        if missing:
            raise BhError("generating the project files failed for %s - see the CMake errors above. If "
                          "Make/Linux/CMakeLists.txt is a small text file instead of a link, the checkout lost "
                          "its symbolic links (copied from Windows?): git config core.symlinks true, then "
                          "check the links out again." % ", ".join(missing))


def newest_source_time() -> float:
    newest = 0.0
    for sub in ("Src", "Util/CompiledNN2ONNX"):
        for dirpath, _, files in os.walk(str(core.ROOT / sub)):
            for name in files:
                if name.endswith((".cpp", ".h", ".hpp", ".c")):
                    try:
                        newest = max(newest, os.path.getmtime(os.path.join(dirpath, name)))
                    except OSError:
                        pass
    return newest


def binary_is_stale(config: str) -> bool:
    b = binary_path(config)
    return b.is_file() and b.stat().st_mtime < newest_source_time()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Rescue robot-only files before rsync --del
# --------------------------------------------------------------------------

def rescue(target: Target, overwrite_calibration: bool, sync_cache: bool) -> None:
    r = core.ssh_run(target.ip, PROBE_SCRIPT, timeout=30)
    if "@end" not in r.out:
        raise BhError(core.explain_ssh_failure(target, r))
    newest = ""
    calib, engines = [], []
    for line in r.out.splitlines():
        key, _, value = line.partition(" ")
        if key == "@newest":
            newest = value.strip()
        elif key == "@calib":
            sha, _, path = value.partition(" ")
            calib.append((sha, path.strip()))
        elif key == "@trt":
            size, _, name = value.partition(" ")
            engines.append((int(size) if size.isdigit() else 0, name.strip()))
    saved_on_robot = bool(newest) and not newest.endswith("settings.cfg")
    for sha, path in calib:
        if not CALIBRATION_FILE.match(path):
            continue
        local = core.ROOT / path
        if local.is_file() and _sha256(local) == sha:
            continue
        # The robot has a calibration this computer does not. It is only "news" if the robot
        # saved it after the last deploy (or we have none at all); otherwise the local file wins.
        if local.is_file() and not saved_on_robot:
            continue
        if overwrite_calibration:
            core.warn("%s: %s on the robot differs and will be overwritten (--overwrite-calibration)"
                      % (target.label, Path(path).name))
            continue
        data = core.ssh_read_file(target.ip, core.REMOTE_HOME + "/" + path)
        if local.is_file():
            backup = local.parent / ".calibration-backup"
            backup.mkdir(exist_ok=True)
            (backup / ("%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), local.name))).write_bytes(local.read_bytes())
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        core.ok("%s: rescued %s saved on the robot -> %s" % (target.label, Path(path).name, path))
    if sync_cache:
        cache = core.config_dir() / "NeuralNets" / "TensorRTCache"
        for size, name in engines:
            if not CACHE_FILE.match(name) or (cache / name).exists():
                continue
            say("  %s: downloading robot-built inference engine %s (%d kB) so the deploy keeps it"
                % (target.label, name, size // 1024))
            cache.mkdir(parents=True, exist_ok=True)
            (cache / name).write_bytes(core.ssh_read_file(target.ip, "%s/NeuralNets/TensorRTCache/%s"
                                                          % (core.REMOTE_CONFIG, name), timeout=600))


# --------------------------------------------------------------------------
# Laptop guard (dead-man switch) and LED mode
# --------------------------------------------------------------------------

def _default_line(representation: str) -> "re.Pattern[str]":
    return re.compile(r"^[ \t]*%s,[ \t]*\r?\n" % representation, re.M)


def _provider_line(representation: str, provider: str) -> "re.Pattern[str]":
    return re.compile(r"^([ \t]*)//[ \t]*(\{representation = %s; provider = %s;\},)" % (representation, provider), re.M)


# The guard's network stop and the dashboard's keyboard driving (RemoteDriveProvider) are switched on together:
# a robot can only be driven from a laptop while that laptop's guard can also stop it.
GUARDED_MODULES = (("EmergencyButtonState", "EmergencyButtonProvider"), ("RemoteDriveRequest", "RemoteDriveProvider"))


def guard_threads_cfg(text: str) -> str:
    """Thread configuration with the network emergency stop and remote driving switched on."""
    for representation, provider in GUARDED_MODULES:
        default, line = _default_line(representation), _provider_line(representation, provider)
        if len(default.findall(text)) != 1 or len(line.findall(text)) != 1:
            raise BhError("this threads.cfg does not have the expected %s lines; cannot arm the guard automatically"
                          % provider)
        text = line.sub(r"\1\2", default.sub("", text))
    return text


def walk_limits(model: str, robot: str, scenario: str, location: str) -> Optional[Tuple[float, float, float, Path]]:
    """The fastest walk the configuration allows: forward mm/s, sideways mm/s, turning deg/s and the file it
    comes from (walkingEngine.cfg, found like the robot finds it). The walk policy is only ever asked for
    what these limits permit, so they are the ceiling of the robot's speed in a game."""
    path = core.find_config("walkingEngine.cfg", model or "k1", robot, scenario, location)
    if path is None:
        return None
    data = core.read_cfg(path)
    numbers = []
    for key in ("x", "y"):
        numbers.append(core.cfg_get(data, "configuredParameters.fastWalkSpeed.maxSpeed.translation." + key))
    numbers.append(core.cfg_get(data, "configuredParameters.fastWalkSpeed.maxSpeed.rotation"))
    match = [re.match(r"-?\d+(\.\d+)?", str(n)) for n in numbers]
    if not all(match):
        return None
    return float(match[0].group()), float(match[1].group()), float(match[2].group()), path


def describe_walk_limits(model: str, robot: str, scenario: str, location: str) -> Optional[str]:
    limits = walk_limits(model, robot, scenario, location)
    if limits is None:
        return None
    x, y, rotation, path = limits
    return "fast walk up to %.0f mm/s forward, %.0f sideways, %.0f deg/s turning (%s)" % (
        x, y, rotation, path.relative_to(core.config_dir()).as_posix())


def warn_if_slower_scenario_exists(model: str, robot: str, scenario: str, location: str) -> None:
    """A robot deployed with the default scenario walks at the speed of Robots/Default; a competition scenario
    may allow much more. Say so, because the choice is easy to forget and costs speed in every game."""
    mine = walk_limits(model, robot, scenario, location)
    if mine is None:
        return
    best = max(((walk_limits(model, robot, s, location), s) for s in core.list_names("Scenarios")),
               key=lambda item: item[0][0] if item[0] else 0, default=(None, ""))
    if best[0] is not None and best[0][0] > mine[0]:
        core.warn("scenario %s limits the fast walk to %.0f mm/s forward, scenario %s allows %.0f (--scenario %s)"
                  % (scenario, mine[0], best[1], best[0][0], best[1]))


def arm_guard(target: Target, scenario: str, location: str) -> None:
    robot = target.robot
    assert robot is not None
    threads = core.find_config("threads.cfg", robot.model, robot.name, scenario, location)
    soccer = core.find_config("optionPlaySoccer.cfg", robot.model, robot.name, scenario, location)
    if threads is None or soccer is None:
        raise BhError("threads.cfg / optionPlaySoccer.cfg not found for scenario %s" % scenario)
    if "HandleStoppedState" not in soccer.read_text(encoding="utf-8"):
        raise BhError("scenario %s does not run HandleStoppedState, so the guard would have no effect" % scenario)
    text = guard_threads_cfg(core.read_exact(threads))
    core.ssh_write_file(target.ip, "%s/Robots/%s/threads.cfg" % (core.REMOTE_CONFIG, robot.name), text.encode("utf-8"))
    core.ok("%s: laptop guard armed - the robot only moves while 'bh guard %s' is running"
            % (target.label, robot.name))


# --------------------------------------------------------------------------
# Deploy
# --------------------------------------------------------------------------

def parse_jobs(specs: Sequence[str]) -> List[Job]:
    jobs = []
    for spec in specs:
        name, sep, number = spec.partition(":")
        player = None
        if sep:
            if not number.isdigit() or int(number) not in PLAYER_RANGE:
                raise BhError("player number in %r must be 1..20" % spec)
            player = int(number)
        target = core.resolve_target(name)
        if target.robot is None or not target.robot.model:
            raise BhError("%s is not a registered robot. Register it first: bh robot add <name> --id <n>" % name)
        jobs.append(Job(target, player))
    players = [j.player for j in jobs if j.player is not None]
    if len(players) != len(set(players)):
        raise BhError("two robots got the same player number")
    if len({j.target.ip for j in jobs}) != len(jobs):
        raise BhError("the same robot is listed twice")
    return jobs


def _option_args(args) -> List[str]:
    out: List[str] = []
    for flag, value in (("-t", args.team), ("-s", args.scenario), ("-l", args.location), ("-w", args.profile),
                        ("-v", args.volume), ("-c", args.color), ("-g", args.goalie_color), ("-m", args.magic)):
        if value is not None:
            out += [flag, str(value)]
    if args.delete_logs:
        out.append("-d")
    return out


def validate_options(args) -> None:
    for kind, value in (("Scenarios", args.scenario), ("Locations", args.location)):
        if value is not None and value not in core.list_names(kind):
            raise BhError("unknown %s %r. Available: %s" % (kind[:-1].lower(), value, ", ".join(core.list_names(kind))))
    if args.profile is not None and not (core.ROOT / "Install" / "Profiles" / args.profile).is_file():
        profiles = sorted(p.name for p in (core.ROOT / "Install" / "Profiles").iterdir() if p.is_file())
        raise BhError("unknown wireless profile %r. Available: %s" % (args.profile, ", ".join(profiles)))
    if args.team is not None and not 0 <= args.team <= 254:
        raise BhError("--team must be 0..254")
    if args.volume is not None and not 0 <= args.volume <= 100:
        raise BhError("--volume must be 0..100")


def verify(job: Job, config: str, args) -> bool:
    """Check that the robot really has what we meant to send."""
    t = job.target
    r = core.ssh_run(t.ip, VERIFY_SCRIPT, timeout=30)
    if "@end" not in r.out:
        core.bad(core.explain_ssh_failure(t, r))
        return False
    fields = {}
    for line in r.out.splitlines():
        key, _, value = line.partition(" ")
        fields[key] = value.strip()
    good = True
    if fields.get("@sha") != _sha256(binary_path(config)):
        core.bad("%s: the binary on the robot is not the one that was built - the copy failed" % t.label)
        good = False
    try:
        cfg = core.parse_cfg(fields.get("@settings", ""))
    except BhError:
        cfg = {}
    expected = {"playerNumber": job.player, "teamNumber": args.team, "scenario": args.scenario, "location": args.location}
    for key, value in expected.items():
        if value is not None and cfg.get(key) != str(value):
            core.bad("%s: %s on the robot is %s, expected %s" % (t.label, key, cfg.get(key), value))
            good = False
    if not fields.get("@ort"):
        core.bad("%s: ONNX Runtime is not linked on the robot (see Docs/robot-onboarding.md)" % t.label)
        good = False
    if good:
        core.ok("%s: verified (team %s, player %s, %s/%s, %s build)"
                % (t.label, cfg.get("teamNumber"), cfg.get("playerNumber"), cfg.get("scenario"),
                   cfg.get("location"), config))
    return good


def deploy(jobs: List[Job], args) -> int:
    config = args.config
    validate_options(args)
    say("== Checking policies")
    if not policy.check(quiet=False):
        raise BhError("policy problems must be fixed before deploying (bh policy list)")
    if args.build:
        build_robot(config)
    elif not binary_path(config).is_file():
        raise BhError("no %s build found at %s - run without --no-build" % (config, binary_path(config)))
    elif binary_is_stale(config):
        core.warn("source files are newer than the %s binary - you are deploying an OLD build (drop --no-build)" % config)
    say("== Checking robots")
    for job in jobs:
        if not core.tcp_open(job.target.ip, timeout=2):
            raise BhError("%s is not reachable" % job.target.label)
        check_hostname(job.target)
        share.respect_claim(job.target, getattr(args, "ignore_claim", False))
        rescue(job.target, args.overwrite_calibration, args.cache_sync)
    if args.dry_run:
        say("Dry run: would now deploy %s to %s" % (config, ", ".join(j.target.label for j in jobs)))
        return 0
    if getattr(args, "backup", False):
        say("== Backing up what is on the robots now")
        for job in jobs:
            share.backup(job.target, "before deploy")
    say("== Copying to %s (robot software stops now - robots go limp)" % ", ".join(j.target.label for j in jobs))
    options = _option_args(args)
    if len(jobs) > 1 and all(j.player is not None for j in jobs):
        multi: List[str] = []
        for j in jobs:
            multi += ["-r", str(j.player), j.target.ip]
        core.run_script("Make/Common/deploy", [config] + multi + ["-nc"] + options)
    else:
        for j in jobs:
            single = [config, j.target.ip, "-nc"] + (["-p", str(j.player)] if j.player is not None else []) + options
            core.run_script("Make/Common/deploy", single)
    say("== Verifying")
    scenario = args.scenario or core.settings().get("scenario", "Default")
    location = args.location or core.settings().get("location", "Default")
    failed = 0
    for job in jobs:
        if not verify(job, config, args):
            failed += 1
            continue
        t = job.target
        robot, model = (t.robot.name, t.robot.model) if t.robot else ("Default", "k1")
        limits = describe_walk_limits(model, robot, scenario, location)
        if limits:
            say("   %s: %s" % (t.label, limits))
        warn_if_slower_scenario_exists(model, robot, scenario, location)
        if args.led:
            led = {"auto": "automatic"}.get(args.led, args.led)
            r = core.ssh_run(t.ip, "sed -i 's/^mode *=.*/mode = %s;/' %s/Scenarios/Default/ledHandler.cfg && "
                                   "grep -q '^mode = %s;' %s/Scenarios/Default/ledHandler.cfg"
                             % (led, core.REMOTE_CONFIG, led, core.REMOTE_CONFIG))
            if r.ok:
                core.ok("%s: back LED mode set to %s" % (t.label, led))
            else:
                core.warn("%s: could not set the LED mode" % t.label)
        if args.guard:
            arm_guard(t, scenario, location)
        if args.restart:
            r = core.ssh_run(t.ip, "systemctl --user start %s" % core.SERVICE, timeout=20)
            if r.ok:
                core.ok("%s: robot software started" % t.label)
            else:
                core.bad("%s: could not start the robot software: %s" % (t.label, r.err.strip()))
                failed += 1
    if failed:
        say("%d robot(s) FAILED - see above." % failed)
        return 1
    if args.restart:
        say("Done. Robots start limp. To stand one up: hold STAND for 2 s (magenta, \"Prepare\"), then WALK for 2 s")
        say("while it is upright. Without a GameController, a short press of STAND or WALK then switches between")
        say("playing and penalized. F1 makes the robot limp again; afterwards hold F1 for 2 s (\"Body ready\").")
        if args.guard:
            say("Guard is armed: run 'bh guard %s' and press ENTER there to let the robots move."
                % " ".join(j.target.robot.name for j in jobs))
    else:
        say("Done. Start with: bh start " + " ".join(j.target.robot.name for j in jobs))
    return 0


def cmd_deploy(args) -> int:
    return deploy(parse_jobs(args.robots), args)


def cmd_build(args) -> int:
    if args.what in ("robot", "all"):
        build_robot(args.config)
    if args.what in ("sim", "all"):
        build_sim(args.config)
    core.ok("build finished")
    return 0


def cmd_generate(args) -> int:
    generate()
    return 0


# --------------------------------------------------------------------------
# Match day
# --------------------------------------------------------------------------

def cmd_match(args) -> int:
    """Release build, strict checks, deploy, verify - one command before a game."""
    jobs = parse_jobs(args.robots)
    if any(j.player is None for j in jobs):
        raise BhError("for a match every robot needs its player number: bh match alice:1 bob:2 ...")
    args.config = "Release"
    args.build = True
    args.restart = True
    args.guard = False          # a normal deploy removes any armed guard
    args.backup = False
    args.ignore_claim = True    # a forgotten claim must never keep a robot out of a game; it is only reported
    args.led = "match"
    args.overwrite_calibration = False
    args.cache_sync = True
    say("== Match pre-flight")
    problems = 0
    try:
        git = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=str(core.ROOT),
                             capture_output=True, text=True)
    except OSError:
        git = None
        core.warn("git is not installed - cannot tell whether the code you are about to play is committed")
    if git is not None and git.returncode == 0 and git.stdout.strip():
        core.warn("uncommitted changes in the repository - you are about to play code that is not in git:")
        for line in git.stdout.strip().splitlines()[:8]:
            say("           " + line)
    if args.profile is None:
        core.warn("no wireless profile given (--profile HSL_A ...): robots keep their current network setup")
    stats = remote.collect_all([j.target for j in jobs])
    for s in stats:
        for level, text in remote.assess(s):
            if level == "fail" and "robot software" not in text:
                core.bad("%s: %s" % (s["name"], text))
                problems += 1
            elif level == "warn" and ("battery" in text or "joint" in text or "GB free" in text):
                core.warn("%s: %s" % (s["name"], text))
    if problems and not args.force:
        raise BhError("%d blocking problem(s). Fix them or repeat with --force." % problems)
    rc = deploy(jobs, args)
    if rc == 0 and not args.dry_run:
        say("")
        say("Match checklist:")
        say("  1. Robots on the field, hold STAND for 2 s, then WALK for 2 s each. Back LED: solid white = GameController heard,")
        say("     blinking white = NOT heard (bh gc doctor). From here on the GameController is in charge.")
        say("  2. Close the dashboard and any ssh sessions - nothing may talk to the robots during the game.")
        say("  3. After the game: bh logs <robot> --download")
    return rc


def _common_deploy_options(p, match: bool) -> None:
    p.add_argument("--team", type=int, help="team number (default: Config/settings.cfg)")
    p.add_argument("--scenario", help="scenario (a directory in Config/Scenarios)")
    p.add_argument("--location", help="location, e.g. the field (a directory in Config/Locations)")
    p.add_argument("--profile", help="wireless profile to activate (a file in Install/Profiles), e.g. HSL_A")
    p.add_argument("--volume", type=int, help="speaker volume in percent (default 100)")
    p.add_argument("--color", help="field player jersey colour")
    p.add_argument("--goalie-color", help="goalkeeper jersey colour")
    p.add_argument("--magic", type=int, help="team communication magic number (-1: derive from this computer)")
    p.add_argument("--delete-logs", action="store_true", help="delete the log files on the robots")
    p.add_argument("--dry-run", action="store_true", help="run all checks but copy nothing")


def add_parsers(sub) -> None:
    p = sub.add_parser("deploy", help="build, copy to robots, verify, start (keeps robot-side calibration safe)",
                       description="Build the robot software and deploy it. Robots are given as name or name:player.")
    p.add_argument("robots", nargs="+", metavar="robot[:player]")
    p.add_argument("-c", "--config", choices=core.BUILD_CONFIGS, default="Develop",
                   help="build configuration (default Develop; Release for games)")
    p.add_argument("--no-build", dest="build", action="store_false", help="deploy the existing binary")
    p.add_argument("--no-restart", dest="restart", action="store_false", help="do not start the robot software")
    p.add_argument("--guard", action="store_true",
                   help="arm the laptop guard: the robot only moves while 'bh guard' sends its heartbeat")
    p.add_argument("--led", choices=["auto", "match", "test"], help="back LED mode (default: as in ledHandler.cfg)")
    p.add_argument("--overwrite-calibration", action="store_true",
                   help="do NOT rescue a calibration that was saved on the robot")
    p.add_argument("--no-cache-sync", dest="cache_sync", action="store_false",
                   help="do not keep inference engines the robot built for itself")
    p.add_argument("--backup", action="store_true",
                   help="first snapshot what is on the robot, so that 'bh robot restore' can put it back")
    p.add_argument("--ignore-claim", action="store_true",
                   help="deploy although somebody else marked the robot as in use (bh robot claim)")
    _common_deploy_options(p, False)
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser("match", help="match day: Release build + pre-flight checks + deploy + verify",
                       description="Everything needed right before a game, in one command.")
    p.add_argument("robots", nargs="+", metavar="robot:player")
    p.add_argument("--force", action="store_true", help="deploy even if the pre-flight finds blocking problems")
    _common_deploy_options(p, True)
    p.set_defaults(func=cmd_match)

    p = sub.add_parser("build", help="compile the robot software and/or the simulator")
    p.add_argument("what", nargs="?", choices=["robot", "sim", "all"], default="robot")
    p.add_argument("-c", "--config", choices=core.BUILD_CONFIGS, default="Develop")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("generate", help="(re)generate the project files after adding or removing source files")
    p.set_defaults(func=cmd_generate)
