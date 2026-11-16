"""GameController protocol (version 20) plus tools to listen, diagnose and to
run a minimal *test* GameController when the official one is not at hand.

Layout source: Util/GameController/include/RoboCupGameControlData.h
(158-byte control packet on UDP 3838, 32-byte robot reply on UDP 3939).
"""

from __future__ import annotations

import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import core
from .core import BhError, say

DATA_PORT = 3838
RETURN_PORT = 3939
HEADER = b"RGme"
VERSION = 20
RETURN_HEADER = b"RGrt"
RETURN_VERSION = 4
MAX_PLAYERS = 20
KICKING_TEAM_NONE = 255

STATES = ["initial", "ready", "set", "playing", "finished"]
PHASES = ["normal", "penalty shoot-out", "extra time", "timeout"]
SET_PLAYS = ["none", "direct free kick", "indirect free kick", "penalty kick", "throw-in", "goal kick", "corner kick"]
PENALTIES = ["none", "illegal positioning", "motion in set", "motion in stop", "local game stuck",
             "incapable robot", "pick-up", "ball holding", "leaving the field", "playing with arms/hands",
             "pushing", "cautioned", "sent off", "substitute"]
COLORS = ["blue", "red", "yellow", "black", "white", "green", "orange", "purple", "brown", "gray"]
PENALTY_PICK_UP = 6
PENALTY_SUBSTITUTE = 13

_HEAD = struct.Struct("<4s10B2h")              # 18 bytes
_TEAM = struct.Struct("<6B2H%dB" % (3 * MAX_PLAYERS))  # 70 bytes
_RETURN = struct.Struct("<4s4B3ff2f")          # 32 bytes
PACKET_SIZE = _HEAD.size + 2 * _TEAM.size
RETURN_SIZE = _RETURN.size
assert PACKET_SIZE == 158 and RETURN_SIZE == 32


def _name(names: List[str], index: int) -> str:
    return names[index] if 0 <= index < len(names) else "unknown(%d)" % index


@dataclass
class Player:
    penalty: int = 0
    secs_till_unpenalised: int = 0
    cautions: int = 0


@dataclass
class Team:
    number: int = 0
    field_player_colour: int = 0
    goalkeeper_colour: int = 1
    goalkeeper: int = 1
    score: int = 0
    penalty_shot: int = 0
    single_shots: int = 0
    message_budget: int = 12000
    players: List[Player] = field(default_factory=lambda: [Player() for _ in range(MAX_PLAYERS)])


@dataclass
class GameData:
    packet_number: int = 0
    players_per_team: int = 5
    competition_type: int = 1
    stopped: int = 0
    game_phase: int = 0
    state: int = 0
    set_play: int = 0
    first_half: int = 1
    kicking_team: int = KICKING_TEAM_NONE
    secs_remaining: int = 600
    secondary_time: int = 0
    teams: List[Team] = field(default_factory=lambda: [Team(), Team()])

    def team(self, number: int) -> Optional[Team]:
        for t in self.teams:
            if t.number == number:
                return t
        return None

    def summary(self) -> str:
        text = "%s%s, %s half, %d:%02d left, team %d %d:%d team %d" % (
            _name(STATES, self.state), " (STOPPED)" if self.stopped else "",
            "first" if self.first_half else "second",
            max(self.secs_remaining, 0) // 60, max(self.secs_remaining, 0) % 60,
            self.teams[0].number, self.teams[0].score, self.teams[1].score, self.teams[1].number)
        if self.set_play:
            text += ", " + _name(SET_PLAYS, self.set_play)
        if self.game_phase:
            text += ", " + _name(PHASES, self.game_phase)
        if self.kicking_team != KICKING_TEAM_NONE:
            text += ", kick-off/kick: team %d" % self.kicking_team
        return text


@dataclass
class ReturnData:
    player: int
    team: int
    fallen: int
    pose: Tuple[float, float, float]
    ball_age: float
    ball: Tuple[float, float]


def encode(g: GameData) -> bytes:
    data = _HEAD.pack(HEADER, VERSION, g.packet_number & 0xFF, g.players_per_team, g.competition_type,
                      g.stopped, g.game_phase, g.state, g.set_play, g.first_half, g.kicking_team & 0xFF,
                      max(-32768, min(32767, g.secs_remaining)), max(-32768, min(32767, g.secondary_time)))
    for t in g.teams:
        flat: List[int] = []
        for p in t.players:
            flat += [p.penalty & 0xFF, max(0, min(255, p.secs_till_unpenalised)), p.cautions & 0xFF]
        data += _TEAM.pack(t.number & 0xFF, t.field_player_colour, t.goalkeeper_colour, t.goalkeeper,
                           t.score & 0xFF, t.penalty_shot & 0xFF, t.single_shots & 0xFFFF,
                           t.message_budget & 0xFFFF, *flat)
    return data


def classify(data: bytes) -> Optional[str]:
    """Why a datagram is not a usable GameController packet (None if it is)."""
    if len(data) < 5 or data[:4] != HEADER:
        return "not a GameController packet"
    if data[4] != VERSION:
        return ("GameController protocol version %d, but this code speaks version %d - "
                "use the GameController release that matches" % (data[4], VERSION))
    if len(data) != PACKET_SIZE:
        return "unexpected packet size %d (expected %d)" % (len(data), PACKET_SIZE)
    return None


def decode(data: bytes) -> GameData:
    problem = classify(data)
    if problem:
        raise ValueError(problem)
    h = _HEAD.unpack_from(data, 0)
    g = GameData(packet_number=h[2], players_per_team=h[3], competition_type=h[4], stopped=h[5],
                 game_phase=h[6], state=h[7], set_play=h[8], first_half=h[9], kicking_team=h[10],
                 secs_remaining=h[11], secondary_time=h[12], teams=[])
    for i in range(2):
        t = _TEAM.unpack_from(data, _HEAD.size + i * _TEAM.size)
        players = [Player(t[8 + 3 * j], t[9 + 3 * j], t[10 + 3 * j]) for j in range(MAX_PLAYERS)]
        g.teams.append(Team(t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], players))
    return g


def decode_return(data: bytes) -> ReturnData:
    if len(data) != RETURN_SIZE or data[:4] != RETURN_HEADER or data[4] != RETURN_VERSION:
        raise ValueError("not a robot return packet")
    r = _RETURN.unpack(data)
    return ReturnData(player=r[2], team=r[3], fallen=r[4], pose=(r[5], r[6], r[7]), ball_age=r[8], ball=(r[9], r[10]))


def encode_return(r: ReturnData) -> bytes:
    return _RETURN.pack(RETURN_HEADER, RETURN_VERSION, r.player, r.team, r.fallen,
                        r.pose[0], r.pose[1], r.pose[2], r.ball_age, r.ball[0], r.ball[1])


def open_listener(port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", port))
    except OSError as e:
        s.close()
        raise BhError("cannot listen on UDP port %d (%s). Is another program (a GameController, "
                      "SimRobot, another bh) already using it?" % (port, e))
    return s


def own_team_number() -> int:
    return int(core.settings().get("teamNumber", "0"))


def broadcast_addresses() -> List[str]:
    """Where to send packets so robots on the cabled /24 and wireless /16 nets see them."""
    result = ["255.255.255.255"]
    for ip in core.local_ipv4():
        parts = ip.split(".")
        candidates = [".".join(parts[:3] + ["255"])]
        if parts[0] == "10":
            candidates.append(".".join(parts[:2] + ["255", "255"]))
        for c in candidates:
            if c not in result:
                result.append(c)
    return result


# --------------------------------------------------------------------------
# Listening
# --------------------------------------------------------------------------

class Listener:
    """Receives GameController packets in a background thread."""

    def __init__(self):
        self.sock = open_listener(DATA_PORT)
        self.sock.settimeout(0.5)
        self.lock = threading.Lock()
        self.last: Optional[GameData] = None
        self.source: Optional[str] = None
        self.last_time = 0.0
        self.problem: Optional[str] = None
        self.sources: Dict[str, float] = {}
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            problem = classify(data)
            with self.lock:
                if problem:
                    if data[:4] == HEADER:
                        self.problem = "%s (from %s)" % (problem, addr[0])
                    continue
                self.last = decode(data)
                self.source = addr[0]
                self.last_time = time.time()
                self.sources[addr[0]] = self.last_time

    def snapshot(self) -> Tuple[Optional[GameData], Optional[str], float, Optional[str]]:
        with self.lock:
            age = time.time() - self.last_time if self.last else float("inf")
            return self.last, self.source, age, self.problem

    def close(self) -> None:
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass


def describe_team(g: GameData, number: int) -> List[str]:
    t = g.team(number)
    if t is None:
        return []
    lines = []
    for i, p in enumerate(t.players[:max(g.players_per_team, 1)]):
        if p.penalty:
            lines.append("    player %d: %s%s" % (i + 1, _name(PENALTIES, p.penalty),
                                                 " (%ds)" % p.secs_till_unpenalised if p.secs_till_unpenalised else ""))
    return lines


def cmd_listen(args) -> int:
    team = own_team_number()
    listener = Listener()
    say("Listening for GameController packets on UDP %d (own team number: %d). Ctrl+C to stop." % (DATA_PORT, team))
    last_text = None
    last_problem = None
    start = time.time()
    try:
        while args.seconds <= 0 or time.time() - start < args.seconds:
            time.sleep(0.25)
            g, source, age, problem = listener.snapshot()
            if problem and problem != last_problem:
                core.warn(problem)
                last_problem = problem
            if g is None:
                continue
            text = g.summary() + (" | LOST (no packet for %.0fs)" % age if age > 3 else "")
            penalties = describe_team(g, team)
            key = text + "".join(penalties)
            if key != last_text:
                say("%s  [from %s] %s" % (time.strftime("%H:%M:%S"), source, text))
                if g.team(team) is None:
                    core.warn("this game is for teams %d and %d - robots with team number %d will ignore it"
                              % (g.teams[0].number, g.teams[1].number, team))
                for line in penalties:
                    say(line)
                last_text = key
    except KeyboardInterrupt:
        say()
    finally:
        listener.close()
    if listener.last is None:
        core.warn("no GameController packet received. Run 'bh gc doctor' for likely causes.")
        return 1
    return 0


def cmd_doctor(args) -> int:
    team = own_team_number()
    problems = 0
    say("GameController connection check (own team number %d from Config/settings.cfg)" % team)
    ips = core.local_ipv4()
    if ips:
        core.ok("this computer's addresses: " + ", ".join(ips))
    else:
        core.bad("this computer has no network address - connect to the field network first")
        problems += 1
    try:
        listener = Listener()
    except BhError as e:
        core.bad(str(e))
        return 1
    say("  ... listening %d s for GameController packets on UDP %d" % (args.seconds, DATA_PORT))
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        time.sleep(0.2)
        if listener.snapshot()[0] is not None and time.time() > deadline - args.seconds + 1.5:
            break
    g, source, age, problem = listener.snapshot()
    sources = dict(listener.sources)
    listener.close()
    if problem:
        core.bad(problem)
        problems += 1
    if g is None:
        if not problem:
            core.bad("no GameController heard. Check, in this order:")
            say("           1. Is the GameController running and is a game started (teams selected)?")
            say("           2. Is this computer on the same network as the GameController?")
            say("           3. Does a firewall block incoming UDP %d? (Windows: allow Python on private networks)" % DATA_PORT)
            say("           No official GameController at hand? 'bh gc test' runs a minimal one for practice.")
            problems += 1
    else:
        core.ok("GameController at %s: %s" % (source, g.summary()))
        if len(sources) > 1:
            core.bad("more than one GameController is sending (%s) - robots will flip between them"
                     % ", ".join(sorted(sources)))
            problems += 1
        own = g.team(team)
        if own is None:
            core.bad("the running game is for teams %d and %d, but this code is configured as team %d. "
                     "Either select the right team in the GameController or deploy with --team <number>."
                     % (g.teams[0].number, g.teams[1].number, team))
            problems += 1
        else:
            core.ok("team %d is in the game (field players %s, goalkeeper %s, goalkeeper is number %d, "
                    "%d players per team)" % (team, _name(COLORS, own.field_player_colour),
                                               _name(COLORS, own.goalkeeper_colour), own.goalkeeper, g.players_per_team))
            for line in describe_team(g, team):
                say("       " + line.strip())
    # Do the robots have the settings that match?
    robots = core.load_robots()
    seen_players: Dict[str, str] = {}
    for robot in robots:
        target = core.resolve_target(robot.name)
        if not core.tcp_open(target.ip):
            core.note("%s is not reachable right now - skipped" % robot.name)
            continue
        r = core.ssh_run(target.ip, "cat %s/settings.cfg" % core.REMOTE_CONFIG, timeout=10)
        if not r.ok:
            core.warn(core.explain_ssh_failure(target, r))
            continue
        cfg = core.parse_cfg(r.out)
        r_team, r_player = cfg.get("teamNumber", "?"), cfg.get("playerNumber", "?")
        if r_team != str(team):
            core.bad("%s is deployed with team number %s (expected %d) - it will ignore the GameController"
                     % (robot.name, r_team, team))
            problems += 1
        elif r_player in seen_players:
            core.bad("%s and %s both have player number %s" % (robot.name, seen_players[r_player], r_player))
            problems += 1
        elif g is not None and r_player.isdigit() and int(r_player) > g.players_per_team:
            core.warn("%s has player number %s but the game has only %d players per team (it counts as a substitute)"
                      % (robot.name, r_player, g.players_per_team))
        else:
            core.ok("%s: team %s, player %s" % (robot.name, r_team, r_player))
        seen_players.setdefault(r_player, robot.name)
    say()
    say("Result: %s" % ("everything fits." if not problems else "%d problem(s) found." % problems))
    say("On the robot itself: in match LED mode a blinking white back LED means 'no GameController heard'.")
    return 1 if problems else 0


# --------------------------------------------------------------------------
# Minimal test GameController
# --------------------------------------------------------------------------

class TestGameController:
    """Sends GameController packets twice a second and collects the robots'
    replies. For practice only - never run it next to the official one."""

    def __init__(self, team: int, opponent: int = 0, players: int = 5, robot_ips: Optional[List[str]] = None):
        self.lock = threading.Lock()
        self.data = GameData(players_per_team=players)
        self.data.teams[0].number = team
        self.data.teams[1].number = opponent
        self.data.teams[1].field_player_colour, self.data.teams[1].goalkeeper_colour = 1, 0
        for t in self.data.teams:
            for i, p in enumerate(t.players):
                if i >= players:
                    p.penalty = PENALTY_SUBSTITUTE
        self.data.kicking_team = team
        self.team = team
        self.destinations = list(dict.fromkeys((robot_ips or []) + broadcast_addresses()))
        self.replies: Dict[int, Tuple[float, str, ReturnData]] = {}
        self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.return_sock = open_listener(RETURN_PORT)
        self.return_sock.settimeout(0.25)
        self.running = False
        self.last_tick = time.time()
        self.threads: List[threading.Thread] = []

    # -- control ----------------------------------------------------------
    def set_state(self, name: str) -> None:
        with self.lock:
            self.data.state = STATES.index(name)
            self.data.set_play = 0
            self.data.secondary_time = 45 if name == "ready" else 0
            if name == "initial":
                self.data.secs_remaining = 600

    def set_stopped(self, stopped: bool) -> None:
        with self.lock:
            self.data.stopped = 1 if stopped else 0

    def penalize(self, player: int, penalty: int = PENALTY_PICK_UP) -> None:
        if not 1 <= player <= MAX_PLAYERS:
            raise BhError("player number must be 1..%d" % MAX_PLAYERS)
        with self.lock:
            p = self.data.teams[0].players[player - 1]
            p.penalty = penalty
            p.secs_till_unpenalised = 45 if penalty else 0

    def goal(self, own: bool) -> None:
        with self.lock:
            scorer = self.data.teams[0 if own else 1]
            scorer.score += 1
            self.data.kicking_team = self.data.teams[1 if own else 0].number
            self.data.state = STATES.index("ready")
            self.data.secondary_time = 45

    def toggle_kickoff(self) -> None:
        with self.lock:
            self.data.kicking_team = (self.data.teams[1].number if self.data.kicking_team == self.data.teams[0].number
                                      else self.data.teams[0].number)

    def toggle_half(self) -> None:
        with self.lock:
            self.data.first_half = 0 if self.data.first_half else 1
            self.data.secs_remaining = 600
            self.data.state = STATES.index("initial")

    def snapshot(self) -> GameData:
        with self.lock:
            return decode(encode(self.data))

    def reply_snapshot(self) -> Dict[int, Tuple[float, str, ReturnData]]:
        with self.lock:
            return dict(self.replies)

    # -- threads ----------------------------------------------------------
    def _tick(self) -> None:
        now = time.time()
        whole = int(now - self.last_tick)
        if whole < 1:
            return
        self.last_tick += whole
        d = self.data
        if d.state == STATES.index("playing") and not d.stopped:
            d.secs_remaining -= whole
        if d.secondary_time > 0:
            d.secondary_time = max(0, d.secondary_time - whole)
        for p in d.teams[0].players:
            if p.penalty not in (0, PENALTY_SUBSTITUTE) and p.secs_till_unpenalised > 0:
                p.secs_till_unpenalised = max(0, p.secs_till_unpenalised - whole)

    def _send_loop(self) -> None:
        while self.running:
            with self.lock:
                self._tick()
                self.data.packet_number = (self.data.packet_number + 1) & 0xFF
                packet = encode(self.data)
            for dest in self.destinations:
                try:
                    self.send_sock.sendto(packet, (dest, DATA_PORT))
                except OSError:
                    pass
            time.sleep(0.5)

    def _return_loop(self) -> None:
        while self.running:
            try:
                data, addr = self.return_sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                r = decode_return(data)
            except ValueError:
                continue
            if r.team == self.team:
                with self.lock:
                    self.replies[r.player] = (time.time(), addr[0], r)

    def start(self) -> None:
        self.running = True
        self.last_tick = time.time()
        for fn in (self._send_loop, self._return_loop):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self.threads.append(t)

    def stop(self) -> None:
        self.running = False
        for t in self.threads:
            t.join(timeout=2)
        for s in (self.send_sock, self.return_sock):
            try:
                s.close()
            except OSError:
                pass


def other_gamecontroller(seconds: float = 2.5) -> Optional[str]:
    """Address of a GameController that is already sending, if any."""
    listener = Listener()
    time.sleep(seconds)
    _, source, _, _ = listener.snapshot()
    listener.close()
    if source and source not in core.local_ipv4() and not source.startswith("127."):
        return source
    return None


def start_test_gc(robots: List[str], players: int, opponent: int, force: bool) -> TestGameController:
    other = None if force else other_gamecontroller()
    if other:
        raise BhError("a GameController is already running at %s. Two controllers would fight over the robots; "
                      "the test controller is only for networks without one." % other)
    ips: List[str] = []
    for r in (core.load_robots() if not robots else [core.resolve_target(s, probe=False).robot for s in robots]):
        if r is not None:
            ips += [ip for ip in (r.lan, r.wlan) if ip]
    gc = TestGameController(own_team_number(), opponent, players, ips)
    gc.start()
    return gc


TEST_HELP = """Commands (type and press Enter):
  i / r / s / p / f     initial / ready / set / playing / finished
  stop  /  go           freeze all robots where they stand / release them
  pen <n> / unpen <n>   penalize (pick-up) / unpenalize player <n>
  goal us | goal them   count a goal and go to ready
  k                     switch the kick-off team
  half                  switch half (back to initial)
  ?                     show robots' replies (pose, ball, fallen)
  q                     quit (robots fall back to their buttons after 5 s)"""


def cmd_test(args) -> int:
    gc = start_test_gc(args.robots, args.players, args.opponent, args.force)
    say("TEST GameController for team %d (%d players). Not for official games." % (gc.team, args.players))
    say("Sending to: " + ", ".join(gc.destinations))
    say(TEST_HELP)
    try:
        while True:
            say("[%s]" % gc.snapshot().summary())
            try:
                line = input("gc> ").strip().lower()
            except EOFError:
                break
            if not line:
                continue
            word, _, rest = line.partition(" ")
            keys = {"i": "initial", "r": "ready", "s": "set", "p": "playing", "f": "finished"}
            try:
                if word in keys:
                    gc.set_state(keys[word])
                elif word in STATES:
                    gc.set_state(word)
                elif word == "stop":
                    gc.set_stopped(True)
                elif word == "go":
                    gc.set_stopped(False)
                elif word in ("pen", "unpen"):
                    gc.penalize(int(rest), PENALTY_PICK_UP if word == "pen" else 0)
                elif word == "goal" and rest in ("us", "them"):
                    gc.goal(rest == "us")
                elif word == "k":
                    gc.toggle_kickoff()
                elif word == "half":
                    gc.toggle_half()
                elif word == "?":
                    replies = gc.reply_snapshot()
                    if not replies:
                        say("  no robot has answered yet (robots answer once per second when they accept the packets)")
                    for player in sorted(replies):
                        when, ip, r = replies[player]
                        say("  player %d (%s, %.0fs ago): %s, at x=%.0f y=%.0f mm, facing %.0f deg, ball %s"
                            % (player, ip, time.time() - when, "FALLEN" if r.fallen == 1 else "upright",
                               r.pose[0], r.pose[1], r.pose[2] * 57.29578,
                               "never seen" if r.ball_age < 0 else "seen %.1fs ago at %.0f/%.0f mm (relative)"
                               % (r.ball_age, r.ball[0], r.ball[1])))
                elif word in ("q", "quit", "exit"):
                    break
                else:
                    say(TEST_HELP)
            except (ValueError, BhError) as e:
                say("  ? %s" % e)
    except KeyboardInterrupt:
        say()
    finally:
        gc.stop()
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("gc", help="GameController: check the connection, watch a game, run a test controller",
                       description="Tools around the GameController (the referee's computer).")
    ps = p.add_subparsers(dest="gc_cmd", metavar="<action>")
    ps.required = True

    q = ps.add_parser("doctor", help="find out why robots do (not) hear the GameController")
    q.add_argument("--seconds", type=int, default=5, help="how long to listen (default 5)")
    q.set_defaults(func=cmd_doctor)

    q = ps.add_parser("listen", help="print the game state whenever it changes")
    q.add_argument("--seconds", type=float, default=0, help="stop after this many seconds (default: run until Ctrl+C)")
    q.set_defaults(func=cmd_listen)

    q = ps.add_parser("test", help="run a minimal test GameController (practice only)")
    q.add_argument("robots", nargs="*", metavar="robot", help="robots to address directly (default: all registered)")
    q.add_argument("--players", type=int, default=5, help="players per team (default 5)")
    q.add_argument("--opponent", type=int, default=0, help="team number of the pretend opponent (default 0)")
    q.add_argument("--force", action="store_true", help="start even if another GameController is heard")
    q.set_defaults(func=cmd_test)
