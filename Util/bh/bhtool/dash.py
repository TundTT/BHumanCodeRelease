"""bh dash: a live dashboard in the browser.

What it shows, and where the data comes from:
  * per robot: software state, battery, joint/computer temperature, load,
    disk, wifi, deployed settings, last log lines   <- one small ssh poll
  * the game: state, score, time, penalties            <- GameController packets
  * with --gc: where each robot thinks it and the ball are, fallen or not
                                                       <- robots' replies to the test GameController
It can also stop/start robots, drive the test GameController and act as the
laptop guard. It serves on 127.0.0.1 only and every request needs the token
from the start URL.

Per-joint angles and temperatures, camera images and the behaviour's
decision tree are available in SimRobot: ``bh connect <robot>``.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional

from . import core, gamecontroller as gcmod, guard as guardmod, remote
from .core import BhError, Target, say

# Must say the same as Src/Modules/BehaviorControl/LEDHandler/LEDHandler.cpp and Docs/leds.md.
LED_LEGEND = [
    ["blue, blinking", "limp (unstiff) - hold STAND 2 s"],
    ["magenta", "getting ready to stand up"],
    ["white", "initial / finished"],
    ["white, blinking", "match mode only: initial / finished and NO GameController heard"],
    ["light blue", "ready (walking to its position)"],
    ["yellow", "set (waiting for kick-off)"],
    ["green", "playing"],
    ["red", "penalized"],
    ["orange", "calibration mode"],
    ["short blue flash", "test mode only: playing, but the ball was not seen for 3 s"],
    ["short red flash", "test mode only: battery low or a joint very hot (dark flash if the LED is red or orange)"],
    ["any colour, slow blinking", "gamepad remote control is active"],
    ["any colour, fast blinking", "cannot do what was requested yet (e.g. still standing up)"],
]


class Dashboard:
    def __init__(self, targets: List[Target], interval: float, passive: bool,
                 test_gc: Optional[gcmod.TestGameController], guard: Optional[guardmod.Guard]):
        self.targets = targets
        self.interval = interval
        self.passive = passive
        self.test_gc = test_gc
        self.guard = guard
        self.token = secrets.token_urlsafe(16)
        self.lock = threading.Lock()
        self.status: Dict[str, dict] = {t.robot.name if t.robot else t.ip: {"name": t.robot.name if t.robot else t.ip,
                                                                         "ip": t.ip, "reachable": None}
                                        for t in targets}
        self.messages: List[str] = []
        self.last_ui_contact = 0.0
        self.running = True
        try:
            self.listener: Optional[gcmod.Listener] = gcmod.Listener()
            self.listener_error = None
        except BhError as e:
            self.listener = None
            self.listener_error = str(e)

    # -- background work -------------------------------------------------
    def poll_loop(self) -> None:
        while self.running:
            if not self.passive:
                for s in remote.collect_all(self.targets):
                    with self.lock:
                        self.status[s["name"]] = s
            # Dead-man: the guard only stays released while a browser tab keeps asking.
            if self.guard and self.guard.released and time.time() - self.last_ui_contact > 2.5:
                self.guard.set_released(False)
                self.log("guard: browser stopped responding -> STOP")
            end = time.time() + self.interval
            while self.running and time.time() < end:
                if self.guard and self.guard.released and time.time() - self.last_ui_contact > 2.5:
                    self.guard.set_released(False)
                    self.log("guard: browser stopped responding -> STOP")
                time.sleep(0.25)

    def log(self, text: str) -> None:
        with self.lock:
            self.messages.append("%s %s" % (time.strftime("%H:%M:%S"), text))
            del self.messages[:-8]

    # -- state for the page ----------------------------------------------
    def state(self) -> dict:
        self.last_ui_contact = time.time()
        own_team = gcmod.own_team_number()
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
        for s in statuses:
            if s.get("reachable"):
                s["assess"] = remote.assess(s)
                s["battery_level"] = next((level for level, text in s["assess"] if text.startswith("battery")), "ok")
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
        return {"time": time.time(), "team": own_team, "robots": robots, "game": game,
                "testgc": self.test_gc is not None, "passive": self.passive,
                "guard": {"enabled": self.guard is not None, "released": bool(self.guard and self.guard.released)},
                "messages": messages, "legend": LED_LEGEND}

    # -- actions from the page -------------------------------------------
    def target(self, name: str) -> Target:
        for t in self.targets:
            if (t.robot.name if t.robot else t.ip) == name:
                return t
        raise BhError("unknown robot %r" % name)

    def service(self, verb: str, targets: List[Target]) -> None:
        def work() -> None:
            for t in targets:
                r = core.ssh_run(t.ip, "systemctl --user %s %s" % (verb, core.SERVICE), timeout=20)
                self.log("%s %s: %s" % (verb, t.label, "ok" if r.ok else core.explain_ssh_failure(t, r)))
        threading.Thread(target=work, daemon=True).start()

    def action(self, body: dict) -> str:
        kind = body.get("action")
        if self.passive and kind in ("stop", "start", "restart", "stop_all"):
            raise BhError("this dashboard is passive (--passive) and does not contact the robots")
        if kind in ("stop", "start", "restart"):
            self.service(kind, [self.target(str(body.get("robot")))])
            return "%s requested" % kind
        if kind == "stop_all":
            if self.guard:
                self.guard.set_released(False)
            if self.test_gc:
                self.test_gc.set_stopped(True)
            self.service("stop", list(self.targets))
            return "HARD STOP sent to all robots"
        if kind == "guard":
            if not self.guard:
                raise BhError("the dashboard was started without --guard")
            self.guard.set_released(bool(body.get("released")))
            self.log("guard: %s" % ("RUN" if self.guard.released else "STOP"))
            return "guard updated"
        if kind and kind.startswith("gc_"):
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
        raise BhError("unknown action %r" % kind)

    def close(self) -> None:
        self.running = False
        if self.guard:
            self.guard.close()
        if self.test_gc:
            self.test_gc.stop()
        if self.listener:
            self.listener.close()


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

        def _allowed(self, token: Optional[str]) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost") and token is not None and secrets.compare_digest(token, dash.token)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            token = dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("t")
            if not self._allowed(token):
                self._send(403, b'{"error": "use the address printed by bh dash"}')
            elif path == "/":
                self._send(200, page, "text/html; charset=utf-8")
            elif path == "/api/state":
                self._send(200, json.dumps(dash.state()).encode("utf-8"))
            else:
                self._send(404, b'{"error": "not found"}')

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(length, 65536)) or b"{}")
            except (ValueError, OSError):
                self._send(400, b'{"error": "bad request"}')
                return
            if not isinstance(body, dict) or not self._allowed(body.get("token")) or self.path != "/api/action":
                self._send(403, b'{"error": "forbidden"}')
                return
            try:
                self._send(200, json.dumps({"ok": dash.action(body)}).encode("utf-8"))
            except (BhError, ValueError) as e:
                self._send(400, json.dumps({"error": str(e)}).encode("utf-8"))

    return Handler


def cmd_dash(args) -> int:
    targets = core.resolve_targets(args.robots) if (args.robots or core.load_robots()) else []
    test_gc = gcmod.start_test_gc(args.robots, args.players, 0, False) if args.gc else None
    guard = None
    if args.guard:
        guard = guardmod.Guard(guardmod.guard_ips([t.robot.name if t.robot else t.ip for t in targets]))
        guard.start()
    dash = Dashboard(targets, args.interval, args.passive, test_gc, guard)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(dash))
    except OSError as e:
        dash.close()
        raise BhError("cannot open port %d (%s) - try --port" % (args.port, e))
    threading.Thread(target=dash.poll_loop, daemon=True).start()
    url = "http://127.0.0.1:%d/?t=%s" % (args.port, dash.token)
    say("Dashboard: %s" % url)
    say("Watching %d robot(s)%s%s%s. Ctrl+C to quit."
        % (len(targets), " (passive: no ssh, GameController only)" if args.passive else "",
           ", test GameController ON" if test_gc else "", ", laptop guard ON (robots stopped until RUN)" if guard else ""))
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        say()
    finally:
        server.server_close()
        dash.close()
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("dash", help="live dashboard in the browser (robots, game state, stop buttons)",
                       description=__doc__, formatter_class=__import__("argparse").RawDescriptionHelpFormatter)
    p.add_argument("robots", nargs="*", metavar="robot", help="default: all registered robots")
    p.add_argument("--gc", action="store_true", help="also run the test GameController (practice only)")
    p.add_argument("--players", type=int, default=5, help="players per team for --gc (default 5)")
    p.add_argument("--guard", action="store_true", help="also act as the laptop guard for robots deployed with --guard")
    p.add_argument("--passive", action="store_true", help="never contact the robots (only listen to the GameController)")
    p.add_argument("--interval", type=float, default=4.0, help="seconds between robot polls (default 4)")
    p.add_argument("--port", type=int, default=8089)
    p.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    p.set_defaults(func=cmd_dash)
