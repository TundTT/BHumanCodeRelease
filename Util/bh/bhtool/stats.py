"""What happens in simulated games: evaluation of the ground-truth recordings.

The simulator writes one row every 100 ms of simulated time (``BH_STATS_FILE=<prefix>``, see
``GameController::writeStats``; ``bh eval --stats PREFIX`` sets it): state of the game, score, the ball and
every robot's position, heading, standing flag and penalty, all in the coordinate system of the first team.
This module turns them into numbers per team: how fast the robots really walk and turn, how often they fall
and how long getting up takes, how hard the ball is kicked, who is near the ball, penalties.
"""

from __future__ import annotations

import csv
import glob
import math
import statistics
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from .core import BhError, say

STATE_PLAYING = 3                         # RoboCupGameControlData.h
FIRST_TEAM_ROBOTS = 20                    # MAX_NUM_PLAYERS: robots 0..19 are the first team, 20..39 the second
PENALTIES = {1: "illegal position", 2: "motion in set", 3: "motion in stop", 4: "local game stuck",
             5: "incapable robot", 6: "pick up", 7: "ball holding", 8: "leaving the field",
             9: "arms/hands", 10: "pushing", 11: "caution", 12: "sent off", 13: "substitute"}
WINDOW_S = 0.5                            # speeds are measured over this time
KICK_MIN_SPEED = 800.0                    # mm/s: slower ball movements are dribbles, not kicks
KICK_MIN_JUMP = 600.0                     # mm/s the ball must gain within one row
KICK_MAX_DISTANCE = 400.0                 # mm: the kicker is this close to the ball
KICK_MAX_SPEED = 8000.0                   # mm/s: faster means the ball was put somewhere (teleport)
MIN_FALL_GAP_S = 5.0                      # after (un)penalizing a robot is put down and may fall: not counted


class Game:
    """One game of a recording: time series per robot and of the ball."""

    def __init__(self, source: str, run: int):
        self.source = source
        self.run = run
        self.t: List[int] = []
        self.state: List[int] = []
        self.score: List[Tuple[int, int]] = []
        self.ball: List[Tuple[float, float]] = []
        self.robots: Dict[int, Dict[str, list]] = {}

    @property
    def dt(self) -> float:
        gaps = [b - a for a, b in zip(self.t, self.t[1:]) if b > a]
        return (statistics.median(gaps) if gaps else 100) / 1000.0


def load(path: str) -> List[Game]:
    """The games in one recording file."""
    games: Dict[int, Game] = {}
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header or header[:10] != ["t", "run", "state", "setPlay", "half", "scoreA", "scoreB",
                                        "ballX", "ballY", "ballZ"]:
            raise BhError("%s is not a recording of the simulator (unexpected header)" % path)
        indices = sorted({int(name[1:name.index("x")]) for name in header[10:] if name.endswith("x")})
        column = {i: header.index("r%dx" % i) for i in indices}
        for row in reader:
            if len(row) != len(header):
                continue                                  # a half-written last line
            game = games.setdefault(int(row[1]), Game(path, int(row[1])))
            game.t.append(int(row[0]))
            game.state.append(int(row[2]))
            game.score.append((int(row[5]), int(row[6])))
            game.ball.append((float(row[7]), float(row[8])))
            for i, c in column.items():
                series = game.robots.setdefault(i, {"x": [], "y": [], "rot": [], "up": [], "pen": []})
                series["x"].append(float(row[c]))
                series["y"].append(float(row[c + 1]))
                series["rot"].append(float(row[c + 2]))
                series["up"].append(row[c + 3] == "1")
                series["pen"].append(int(row[c + 4]))
    return [games[k] for k in sorted(games)]


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    k = (len(ordered) - 1) * p / 100.0
    low, high = int(math.floor(k)), int(math.ceil(k))
    return ordered[low] + (ordered[high] - ordered[low]) * (k - low)


def _angle_diff(a: float, b: float) -> float:
    return (a - b + math.pi) % (2 * math.pi) - math.pi


def analyze(game: Game) -> List[dict]:
    """Numbers for the first and the second team of one game (index 0 and 1)."""
    dt = game.dt
    n = len(game.t)
    window = max(1, int(round(WINDOW_S / dt)))
    playing = [s == STATE_PLAYING for s in game.state]
    teams = [{"robots": [i for i in game.robots if i < FIRST_TEAM_ROBOTS]},
             {"robots": [i for i in game.robots if i >= FIRST_TEAM_ROBOTS]}]
    for team in teams:
        team.update(speeds=[], turns=[], falls=0, getups=[], samples=0, down=0, idle=0, moving=0, distance=0.0,
                    kicks=[], nearest=0, close=0, penalties={})
    time_playing = sum(playing) * dt
    ball = game.ball

    for t, team in enumerate(teams):
        for i in team["robots"]:
            r = game.robots[i]
            fell_at: Optional[int] = None
            last_pen_change = -10 ** 9
            for k in range(1, n):
                if r["pen"][k] != r["pen"][k - 1]:
                    last_pen_change = k
                    if r["pen"][k] and playing[k]:
                        name = PENALTIES.get(r["pen"][k], str(r["pen"][k]))
                        team["penalties"][name] = team["penalties"].get(name, 0) + 1
                if not playing[k] or r["pen"][k]:
                    fell_at = None
                    continue
                team["samples"] += 1
                if not r["up"][k]:
                    team["down"] += 1
                if r["up"][k - 1] and not r["up"][k] and (k - last_pen_change) * dt > MIN_FALL_GAP_S:
                    team["falls"] += 1
                    fell_at = k
                elif not r["up"][k - 1] and r["up"][k] and fell_at is not None:
                    team["getups"].append((k - fell_at) * dt)
                    fell_at = None
                if k >= window and all(playing[j] and r["up"][j] and not r["pen"][j] for j in range(k - window, k + 1)):
                    step = math.hypot(r["x"][k] - r["x"][k - window], r["y"][k] - r["y"][k - window])
                    speed = step / (window * dt)
                    turn = abs(_angle_diff(r["rot"][k], r["rot"][k - window])) / (window * dt)
                    team["speeds"].append(speed)
                    team["turns"].append(math.degrees(turn))
                    team["idle" if speed < 100 else "moving"] += 1
                    if speed >= 100:
                        team["distance"] += speed * dt
            # distance walked is estimated from moving samples only (standing sway is not walking)

    # who is near the ball (nearest robot of all, unpenalized)
    for k in range(n):
        if not playing[k]:
            continue
        best = None
        for i, r in game.robots.items():
            if r["pen"][k]:
                continue
            d = math.hypot(r["x"][k] - ball[k][0], r["y"][k] - ball[k][1])
            if best is None or d < best[0]:
                best = (d, i)
        if best:
            teams[0 if best[1] < FIRST_TEAM_ROBOTS else 1]["nearest"] += 1
            if best[0] < 500:
                teams[0 if best[1] < FIRST_TEAM_ROBOTS else 1]["close"] += 1

    # kicks: the ball suddenly gets fast while a robot is next to it
    speeds = [0.0] + [math.hypot(ball[k][0] - ball[k - 1][0], ball[k][1] - ball[k - 1][1]) / dt for k in range(1, n)]
    last_kick = -10 ** 9
    for k in range(1, n):
        if not (playing[k] and playing[k - 1]) or speeds[k] > KICK_MAX_SPEED:
            continue
        if speeds[k] >= KICK_MIN_SPEED and speeds[k] - speeds[k - 1] >= KICK_MIN_JUMP and (k - last_kick) * dt > 0.5:
            near = None
            for i, r in game.robots.items():
                if r["pen"][k - 1]:
                    continue
                d = math.hypot(r["x"][k - 1] - ball[k - 1][0], r["y"][k - 1] - ball[k - 1][1])
                if near is None or d < near[0]:
                    near = (d, i)
            if near and near[0] <= KICK_MAX_DISTANCE:
                peak = max(speeds[k:k + 4])
                if peak <= KICK_MAX_SPEED:
                    teams[0 if near[1] < FIRST_TEAM_ROBOTS else 1]["kicks"].append(peak)
                    last_kick = k

    result = []
    for t, team in enumerate(teams):
        robots = max(1, len(team["robots"]))
        total = max(1, team["samples"])
        result.append({
            "goals": game.score[-1][t] if game.score else 0,
            "seconds": time_playing,
            "falls": team["falls"] / robots * 1.0,                 # per robot and game
            "fallen %": 100.0 * team["down"] / total,
            "getup s": statistics.mean(team["getups"]) if team["getups"] else float("nan"),
            "getups": team["getups"],
            "speed p50": percentile(team["speeds"], 50), "speed p95": percentile(team["speeds"], 95),
            "speed p99": percentile(team["speeds"], 99), "speeds": team["speeds"],
            "turn p95": percentile(team["turns"], 95), "turns": team["turns"],
            "idle %": 100.0 * team["idle"] / max(1, team["idle"] + team["moving"]),
            "walked m": team["distance"] / 1000.0 / robots,
            "kicks": len(team["kicks"]), "kick speeds": team["kicks"],
            "ball nearest %": 100.0 * team["nearest"] / max(1, sum(playing)),
            "ball within 50 cm %": 100.0 * team["close"] / max(1, sum(playing)),
            "penalties": team["penalties"],
        })
    return result


def files_for(prefix: str) -> List[str]:
    return sorted(glob.glob(prefix + "_*.csv"))


def _mean(values: Sequence[float]) -> float:
    values = [v for v in values if not math.isnan(v)]
    return statistics.mean(values) if values else float("nan")


def _fmt(v: float, digits: int = 1) -> str:
    return "-" if v != v else ("%.*f" % (digits, v))


def _pool(rows: Sequence[dict], key: str) -> List[float]:
    return [v for row in rows for v in row[key]]


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = len(xs)
    if n < 5:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else float("nan")


def summarize(prefixes: Sequence[str]) -> List[Tuple[str, List[dict], List[dict]]]:
    """For every prefix: the per-game numbers of the 'test' team and of the 'reference' team.

    A recording of an A/B run (``bh eval --variant``) has the suffix ``-varA`` or ``-varB``: the variant is the
    first or the second team. Any other recording is a plain run: the first team is the 'test' team."""
    expanded = []
    for prefix in prefixes:                      # "base" stands for base-varA and base-varB of an A/B run
        expanded += [prefix] if files_for(prefix) else [prefix + "-varA", prefix + "-varB"]
    result = []
    for prefix in expanded:
        tests: List[dict] = []
        refs: List[dict] = []
        for path in files_for(prefix):
            variant_second = prefix.endswith("-varB")
            for game in load(path):
                if sum(s == STATE_PLAYING for s in game.state) * game.dt < 60:
                    continue                                    # a game that was cut off right at the start
                a, b = analyze(game)
                tests.append(b if variant_second else a)
                refs.append(a if variant_second else b)
        result.append((prefix, tests, refs))
    return result


def report(prefixes: Sequence[str]) -> int:
    groups = summarize(prefixes)
    pooled_tests = [r for _, tests, _ in groups for r in tests]
    pooled_refs = [r for _, _, refs in groups for r in refs]
    if not pooled_tests:
        raise BhError("no games found for %s (recordings are named PREFIX_<process id>.csv)" % ", ".join(prefixes))
    say("%d games, playing time %.0f s each (simulated)" % (len(pooled_tests), _mean([g["seconds"] for g in pooled_tests])))
    say()
    say("%-34s %10s %10s" % ("per team, mean over games", "test", "reference"))
    rows = [("goals", "goals", 2), ("falls per robot", "falls", 2), ("time lying down, %", "fallen %", 1),
            ("getting up takes, s", "getup s", 1), ("walking speed, median mm/s", "speed p50", 0),
            ("walking speed, 95th pct mm/s", "speed p95", 0), ("walking speed, 99th pct mm/s", "speed p99", 0),
            ("turning, 95th pct deg/s", "turn p95", 0), ("standing still (<100 mm/s), %", "idle %", 1),
            ("walked per robot, m", "walked m", 1), ("kicks (ball touched hard)", "kicks", 1),
            ("ball nearest to the team, %", "ball nearest %", 1), ("ball within 50 cm, %", "ball within 50 cm %", 1)]
    for label, key, digits in rows:
        say("%-34s %10s %10s" % (label, _fmt(_mean([g[key] for g in pooled_tests]), digits),
                                 _fmt(_mean([g[key] for g in pooled_refs]), digits)))
    say()
    say("how hard the ball is kicked (mm/s at the moment after the kick, all kicks):")
    for label, games in (("test", pooled_tests), ("reference", pooled_refs)):
        kicks = _pool(games, "kick speeds")
        if kicks:
            bins = [sum(1 for v in kicks if lo <= v < hi) for lo, hi in ((0, 1000), (1000, 2000), (2000, 3000), (3000, 1e9))]
            say("  %-10s median %4.0f  90th pct %4.0f  max %4.0f   <1000: %d  1000-2000: %d  2000-3000: %d  >3000: %d"
                % (label, percentile(kicks, 50), percentile(kicks, 90), max(kicks), *bins))
    penalties: Dict[str, List[int]] = {}
    for side, games in ((0, pooled_tests), (1, pooled_refs)):
        for g in games:
            for name, count in g["penalties"].items():
                penalties.setdefault(name, [0, 0])[side] += count
    if penalties:
        say()
        say("penalties (total over all games): " + ", ".join("%s %d/%d" % (n, c[0], c[1]) for n, c in sorted(penalties.items())))
    if len(pooled_tests) >= 10:
        say()
        say("what goes with winning (correlation over games of test-minus-reference with the goal difference;")
        say("only an association: n=%d, |r| below %.2f is noise)" % (len(pooled_tests), 2 / math.sqrt(len(pooled_tests))))
        goal_diff = [a["goals"] - b["goals"] for a, b in zip(pooled_tests, pooled_refs)]
        for label, key, _ in rows[1:]:
            diffs = [a[key] - b[key] for a, b in zip(pooled_tests, pooled_refs)]
            r = _pearson([d for d in diffs], goal_diff) if all(d == d for d in diffs) else float("nan")
            say("  %-34s r = %s" % (label, _fmt(r, 2)))
    return 0


def cmd_stats(args) -> int:
    return report(args.prefix)


def add_parser(sub) -> None:
    p = sub.add_parser("stats", help="numbers from recorded simulated games (speed, falls, kicks, ...)",
                       description="Evaluate recordings made with 'bh eval --stats PREFIX'. Several prefixes are "
                                   "pooled; an A/B run leaves PREFIX-varA and PREFIX-varB (the variant is the first/"
                                   "second team), both are read when you name PREFIX.")
    p.add_argument("prefix", nargs="+", help="recording prefix, e.g. Build/Stats/base")
    p.set_defaults(func=cmd_stats)
