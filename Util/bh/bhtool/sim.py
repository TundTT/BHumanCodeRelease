"""SimRobot front end: start a simulated game, attach to a real robot, or
run many headless games to compare two versions of the code."""

from __future__ import annotations

import csv
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from . import core, deploy
from .core import BhError, say

SCENE_HINTS = {
    "Game": "5 vs 5 with real vision (slow, most realistic)",
    "GameFast": "5 vs 5, vision replaced by ground truth + noise (fast, for behaviour work)",
    "GamePerceptOracle": "5 vs 5 with oracle percepts (in between)",
    "Game11v11": "11 vs 11 on the large field",
    "Game3v3M": "3 vs 3 on the medium field",
    "Robot": "one robot on an empty field",
    "RobotFast": "one robot, fast simulation",
    "Remote": "attach to a real robot (use: bh connect <robot>)",
    "Replay": "replay a log file",
}


def scene_dir(model: str) -> Path:
    return core.config_dir() / "Scenes" / core.MODELS[model]


def scenes(model: str) -> List[str]:
    d = scene_dir(model)
    return sorted(p.stem for p in d.glob("*.ros3")) if d.is_dir() else []


def simrobot(config: str, build: bool) -> Path:
    exe = deploy.simrobot_path(config)
    if build or not exe.exists():
        if not build:
            say("SimRobot (%s) has not been built yet - building it now." % config)
        deploy.build_sim(config)
    if not exe.exists():
        raise BhError("SimRobot not found at %s" % exe)
    return exe


def cmd_sim(args) -> int:
    if args.list:
        for model in ("k1", "t1"):
            say("%s scenes:" % model.upper())
            for name in scenes(model):
                say("  %-26s %s" % (name, SCENE_HINTS.get(name, "")))
        return 0
    names = scenes(args.model)
    match = [n for n in names if n.lower() == args.scene.lower()]
    if not match:
        raise BhError("unknown scene %r for %s. Available: %s" % (args.scene, args.model.upper(), ", ".join(names)))
    exe = simrobot(args.config, args.build)
    scene = scene_dir(args.model) / (match[0] + ".ros3")
    say("Starting SimRobot with %s. In its console: 'gc playing' starts the game, 'help' lists commands."
        % scene.relative_to(core.ROOT).as_posix())
    return subprocess.call([str(exe)] + (["-noWindow"] if args.headless else []) + [str(scene)])


def cmd_connect(args) -> int:
    """Full mission control for one real robot: SimRobot's Remote scene shows camera
    images, the world model, the behaviour's decisions, joint angles, temperatures..."""
    t = core.resolve_target(args.robot)
    if t.robot is None or not t.robot.model:
        raise BhError("%s is not a registered robot" % args.robot)
    if not core.tcp_open(t.ip, timeout=2):
        raise BhError("%s is not reachable" % t.label)
    exe = simrobot(args.config, False)
    connect = core.config_dir() / "Scenes" / "Includes" / "connect.con"
    core.write_exact(connect, "sc Remote %s\n" % t.ip)
    scene = scene_dir(t.robot.model) / "Remote.ros3"
    say("Connecting SimRobot to %s. Useful console commands are listed in Docs/dashboard.md." % t.label)
    try:
        return subprocess.call([str(exe), str(scene)])
    finally:
        try:
            connect.unlink()
        except OSError:
            pass


def eval_script(text: str) -> str:
    """The console script for 'test game ... con <file>' the way SimRobot wants it: a .con
    file given relative to Config/Scenes (see TestCommandParser::parseUserConfigPath)."""
    base = (core.config_dir() / "Scenes").resolve()
    given = Path(text)
    found = next((p.resolve() for p in ([given] if given.is_absolute() else [Path.cwd() / given, base / given])
                  if p.is_file()), None)
    if found is None:
        raise BhError("script %s not found (looked in the current directory and in Config/Scenes)" % text)
    if found.suffix != ".con":
        raise BhError("the script must be a SimRobot console script (*.con): %s" % text)
    try:
        relative = found.relative_to(base).as_posix()
    except ValueError:
        raise BhError("the script must be inside Config/Scenes (the simulator resolves it from there): %s" % found)
    if any(c.isspace() for c in relative):
        raise BhError("the script's path must not contain spaces: %s" % relative)
    return relative


def eval_command(games: int, script: str = "") -> str:
    """The simulator's test command for a number of games."""
    if games < 1:
        raise BhError("--games must be at least 1")
    return "test game %d%s" % (games, " con " + script if script else "")


def flatten_cfg(text: str) -> str:
    """A configuration file as the single line the console command 'set' takes."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return " ".join(text.split())


def team_robots(scene: str, second: bool) -> List[str]:
    """Names of the simulated robots of one team (see Config/Scenes/Includes/{3vs3,5vs5,11vs11}.con)."""
    size = 11 if "11v11" in scene else 3 if "3v3" in scene else 5
    return ["robot%d" % (i + (21 if second else 1)) for i in range(size)]


def parse_variants(specs: Sequence[str]) -> List[Tuple[str, Path]]:
    """--variant Module=file.cfg -> [(name for the console command 'set', path)]. The file is the complete
    parameter set. A plain name is a module; other parameter sets are named as the code registers them,
    e.g. behavior:PlayBall for MODIFY("behavior:PlayBall", ...) of the role read from rolePlayBall.cfg."""
    result = []
    for spec in specs:
        module, sep, file = spec.partition("=")
        path = Path(file)
        if not sep or not re.fullmatch(r"([A-Za-z][A-Za-z0-9]*:)*[A-Za-z][A-Za-z0-9]*", module):
            raise BhError("--variant expects Module=file.cfg, e.g. WalkingEngine=my/walkingEngine.cfg (got %r)" % spec)
        if not path.is_file():
            raise BhError("variant file %s not found" % file)
        core.parse_cfg(core.read_exact(path))        # refuse files that are not valid configuration
        result.append((module if ":" in module else "module:" + module, path))
    return result


def variant_script(variants: Sequence[Tuple[str, Path]], robots: Sequence[str]) -> str:
    """Console script that gives the named robots other module parameters."""
    lines = ["robot " + " ".join(robots)]
    lines += ["set %s %s" % (name, flatten_cfg(core.read_exact(path))) for name, path in variants]
    return "\n".join(lines + ["robot all", ""])


SCENARIOS = re.compile(r'(<Compound name="scenarios">\s*)((?:<Compound name="[^"]+"/>\s*)+)(</Compound>)')


def scene_scenarios(text: str) -> List[str]:
    """The scenarios a scene description (*.ros3) names: one for both teams, or one per team."""
    found = SCENARIOS.search(text)
    return re.findall(r'name="([^"]+)"', found.group(2)) if found else []


def scene_with_scenarios(text: str, first: str, second: str) -> str:
    """A scene description in which the first and the second team use the given scenarios
    (RoboCupCtrl reads the children of the compound "scenarios" in this order)."""
    found = SCENARIOS.search(text)
    if not found:
        raise BhError("the scene does not name its scenario (<Compound name=\"scenarios\">), so the teams "
                      "cannot be given different ones")
    indent = re.search(r" *$", found.group(1)).group(0)
    children = "".join('<Compound name="%s"/>\n%s' % (name, indent if i == 0 else indent[:-2])
                       for i, name in enumerate((first, second)))
    return text[:found.start(2)] + children + text[found.start(3):]


def final_scores(rows: Sequence[Sequence[str]]) -> List[Tuple[int, int]]:
    """(goals of team A, goals of team B) per game from the runner's table. The score in the
    row of the second half is the score of the whole game (the GameController keeps counting)."""
    return [(int(r[3]), int(r[4])) for r in rows if len(r) >= 5 and r[1].strip() == "2"]


def judge(diffs: Sequence[int]) -> Tuple[float, float, str]:
    """Mean and standard error of goal differences, and what they allow to say."""
    n = len(diffs)
    mean = sum(diffs) / n if n else 0.0
    if n < 2:
        return mean, float("inf"), "too few games to say anything"
    error = math.sqrt(sum((d - mean) ** 2 for d in diffs) / (n - 1) / n)
    if mean - 2 * error > 0:
        return mean, error, "BETTER (more than two standard errors above zero)"
    if mean + 2 * error < 0:
        return mean, error, "WORSE (more than two standard errors below zero)"
    return mean, error, "no clear difference - noise is larger than the effect; play more games to be sure"


def run_games(scene: str, config: str, testcmd: str, workers: Optional[int],
              stats: Optional[str] = None) -> List[Tuple[int, int]]:
    """Run the game runner and return the final score of every game."""
    out = core.ROOT / "Build" / "Eval" / ("%x-%d" % (int(time.time() * 1000), os.getpid()))   # one per run
    table_dir = out / "b-human"                     # the runner writes into <temporary directory>/b-human
    out.mkdir(parents=True)
    cmd = [sys.executable, str(core.ROOT / "Util" / "TestRunner" / "runner.py"), scene,
           "--env", config.lower(), "--testcmd", testcmd, "--loglevel", "info"]
    if workers:
        cmd += ["--workers", str(workers)]
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        if not shutil.which("xvfb-run"):            # SimRobot needs a display even without a window
            raise BhError("no display available. Install a virtual one (sudo apt install xvfb) or run this "
                          "from a desktop session.")
        cmd = ["xvfb-run", "-a"] + cmd
    env = dict(os.environ, TMPDIR=str(out), TEMP=str(out), TMP=str(out))
    if stats:                                       # the simulator records the ground truth (see bh stats)
        Path(stats).parent.mkdir(parents=True, exist_ok=True)
        env["BH_STATS_FILE"] = str(Path(stats).resolve())
    rc = subprocess.call(cmd, cwd=str(core.ROOT / "Util" / "TestRunner"), env=env)
    new = sorted(table_dir.glob("*_results.csv"))
    if rc != 0 or not new:
        raise BhError("the game runner failed (exit code %d)" % rc)
    with open(new[-1], newline="") as f:
        return final_scores(list(csv.reader(f)))


def cmd_eval(args) -> int:
    """Play headless simulated games and report the scores (needs the 'psutil' package)."""
    names = scenes(args.model)
    match = [n for n in names if n.lower() == args.scene.lower()]
    if not match:
        raise BhError("unknown scene %r. Available: %s" % (args.scene, ", ".join(names)))
    variants = parse_variants(args.variant or [])
    compare = bool(variants or args.scenario)
    if compare and args.con:
        raise BhError("--variant and --scenario write their own scripts; they cannot be combined with --con")
    if compare and (args.games < 2 or args.games % 2):
        raise BhError("a comparison needs an even number of games: the variant plays half of them as each team")
    scene_dir = core.config_dir() / "Scenes" / core.MODELS[args.model]
    scene_text = core.read_exact(scene_dir / (match[0] + ".ros3"))
    if args.scenario:
        if not (core.config_dir() / "Scenarios" / args.scenario).is_dir():
            raise BhError("there is no scenario Config/Scenarios/%s" % args.scenario)
        current = scene_scenarios(scene_text)
        if len(current) != 1:
            raise BhError("scene %s must name exactly one scenario to compare another one with it" % match[0])
        if current[0] == args.scenario:
            raise BhError("scene %s already uses the scenario %s" % (match[0], args.scenario))
    testcmd = eval_command(args.games, eval_script(args.con) if args.con else "")
    try:
        import psutil  # noqa: F401  (used by Util/TestRunner/runner.py)
    except ImportError:
        raise BhError("the game runner needs the Python package 'psutil':  %s -m pip install psutil"
                      % Path(sys.executable).name)
    simrobot(args.config, args.build)
    scene = "Config/Scenes/%s/%s.ros3" % (core.MODELS[args.model], match[0])
    say("Playing %d simulated game(s) in %s (this takes a while; each game is two 10-minute halves "
        "of simulated time)." % (args.games, match[0]))
    if not compare:
        if args.con:
            say("Every game starts by running %s." % testcmd.split(" con ")[1])
        scores = run_games(scene, args.config, testcmd, args.workers, args.stats)
        mean, error, _ = judge([a - b for a, b in scores])
        say("Final scores A:B  %s" % "  ".join("%d:%d" % s for s in scores))
        say("Team A minus team B: %+.2f goals per game (standard error %.2f) over %d game(s)."
            % (mean, error, len(scores)))
        return 0

    # The variant plays half of the games as team A and half as team B, so that an advantage
    # of one side of the pitch or of the kick-off cannot be mistaken for an effect of the variant.
    scripts = core.config_dir() / "Scenes" / "Eval"
    scripts.mkdir(exist_ok=True)
    tag = "%x%d" % (int(time.time()), os.getpid())
    what = ", ".join([name for name, _ in variants] + (["scenario " + args.scenario] if args.scenario else []))
    written: List[Path] = []
    diffs: List[int] = []
    goals = [0, 0]
    try:
        for second in (False, True):
            side = "B" if second else "A"
            half_scene, script_name = scene, ""
            if variants:
                script = scripts / ("variant-%s-%s.con" % (tag, side))
                core.write_exact(script, variant_script(variants, team_robots(match[0], second)))
                written.append(script)
                script_name = eval_script(str(script))
            if args.scenario:                        # a copy of the scene whose teams use different scenarios
                name = "%sEval%s%s" % (match[0], tag, side)
                teams = (current[0], args.scenario) if second else (args.scenario, current[0])
                core.write_exact(scene_dir / (name + ".ros3"), scene_with_scenarios(scene_text, *teams))
                shutil.copyfile(scene_dir / (match[0] + ".con"), scene_dir / (name + ".con"))
                written += [scene_dir / (name + ".ros3"), scene_dir / (name + ".con")]
                half_scene = "Config/Scenes/%s/%s.ros3" % (core.MODELS[args.model], name)
            say("== Variant (%s) plays as team %s" % (what, side))
            for a, b in run_games(half_scene, args.config, eval_command(args.games // 2, script_name), args.workers,
                                  args.stats + "-var" + side if args.stats else None):
                mine, theirs = (b, a) if second else (a, b)
                diffs.append(mine - theirs)
                goals[0] += mine
                goals[1] += theirs
    finally:
        for file in written:                         # another evaluation may be using the directory
            file.unlink(missing_ok=True)
        if not any(scripts.iterdir()):
            scripts.rmdir()
    mean, error, verdict = judge(diffs)
    wins, losses = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    say()
    say("Variant against the current configuration, %d games:" % len(diffs))
    say("  goals            %d : %d" % (goals[0], goals[1]))
    say("  won/drawn/lost   %d / %d / %d" % (wins, len(diffs) - wins - losses, losses))
    say("  goal difference  %+.2f per game (standard error %.2f)" % (mean, error))
    say("  verdict          %s" % verdict)
    return 0


def add_parsers(sub) -> None:
    p = sub.add_parser("sim", help="start the simulator with a game scene")
    p.add_argument("scene", nargs="?", default="GameFast", help="scene name (default GameFast; see --list)")
    p.add_argument("--model", choices=["k1", "t1"], default="k1")
    p.add_argument("-c", "--config", choices=core.BUILD_CONFIGS, default="Develop")
    p.add_argument("--build", action="store_true", help="rebuild the simulator first")
    p.add_argument("--headless", action="store_true", help="run without a window")
    p.add_argument("--list", action="store_true", help="list the available scenes")
    p.set_defaults(func=cmd_sim)

    p = sub.add_parser("connect", help="mission control for one real robot (SimRobot attached over the network)")
    p.add_argument("robot")
    p.add_argument("-c", "--config", choices=core.BUILD_CONFIGS, default="Develop")
    p.set_defaults(func=cmd_connect)

    p = sub.add_parser("eval", help="play headless simulated games and print the scores")
    p.add_argument("--games", type=int, default=4, help="number of games (default 4)")
    p.add_argument("--scene", default="GameFast")
    p.add_argument("--model", choices=["k1", "t1"], default="k1")
    p.add_argument("-c", "--config", choices=core.BUILD_CONFIGS, default="Develop")
    p.add_argument("--workers", type=int, help="parallel simulator processes (default: one per CPU core)")
    p.add_argument("--con", metavar="FILE.con",
                   help="console script in Config/Scenes that runs at the start of every game, e.g. to give "
                        "one team different parameters (see Docs/simulation.md)")
    p.add_argument("--variant", action="append", metavar="Module=FILE.cfg",
                   help="A/B test: one team plays with this complete parameter file for the module (e.g. "
                        "WalkingEngine=my/walkingEngine.cfg; other parameter sets by their console name, e.g. "
                        "behavior:PlayBall=my/rolePlayBall.cfg), half of the games as each team; repeatable")
    p.add_argument("--scenario", metavar="NAME",
                   help="A/B test: one team plays with the scenario Config/Scenarios/NAME instead of the "
                        "scene's own (any configuration file can differ there), half of the games as each team")
    p.add_argument("--stats", metavar="PREFIX",
                   help="also record the ground truth of every game to PREFIX_<pid>.csv (an A/B run: PREFIX-varA_..., "
                        "PREFIX-varB_...); evaluate with bh stats PREFIX")
    p.add_argument("--build", action="store_true", help="rebuild the simulator first")
    p.set_defaults(func=cmd_eval)
