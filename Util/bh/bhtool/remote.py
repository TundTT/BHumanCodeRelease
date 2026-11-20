"""Talk to robots over ssh: status, start/stop, logs, shell, power.

``collect()`` is the single place that gathers a robot's state; ``bh status``,
the match pre-flight and the dashboard all use it. It runs one small script
per robot and reads only cheap sources (/proc, /sys and the status file the
robot software writes anyway), so it does not disturb a running robot.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Sequence, Tuple

from . import core, share
from .core import BhError, Target, say

# Limits taken from Config/Robots/Default/*/boosterProvider.cfg and robotHealthProvider.cfg.
TEMP_HOT, TEMP_VERY_HOT, TEMP_CRITICAL = 76, 88, 100
CPU_HOT = 85
BATTERY_LOW = {"k1": 40, "t1": 20, "": 30}
BOOSTER_BATTERY_MAX_AGE = 300   # s; Booster's battery reading (taken while the robot software is off) is older

STATUS_SCRIPT = r'''
cd /home/booster 2>/dev/null
h=$(hostname)
echo "@host $h"
echo "@service $(systemctl --user is-active bhuman.service 2>/dev/null)"
echo "@internal $({ cat /var/volatile/tmp/internalState.txt 2>/dev/null || cat /run/user/*/internalState.txt 2>/dev/null; } | head -n 1)"
for z in /sys/devices/virtual/thermal/thermal_zone*/temp; do [ -r "$z" ] && echo "@zone $(cat "$z")"; done
echo "@disk $(df -Pk /home/booster 2>/dev/null | awk 'NR==2 {print $4, $5}')"
echo "@logs $(ls logs 2>/dev/null | wc -l)"
echo "@dump $([ -e bhdump.log ] && echo yes || echo no)"
echo "@newest $(ls -t Config/settings.cfg Config/Robots/*/cameraCalibration.cfg Config/Robots/*/imuCalibration.cfg 2>/dev/null | head -1)"
echo "@settings $(tr '\n' ' ' 2>/dev/null <Config/settings.cfg)"
echo "@wifi $(awk 'NR>2 {gsub(/:/, "", $1); gsub(/\./, "", $3); gsub(/\./, "", $4); print $1, $3, $4; exit}' /proc/net/wireless 2>/dev/null)"
echo "@load $(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)"
echo "@mem $(awk '/MemTotal/ {t=$2} /MemAvailable/ {a=$2} END {if (t) printf "%d", (t-a)*100/t}' /proc/meminfo 2>/dev/null)"
echo "@uptime $(cut -d. -f1 /proc/uptime 2>/dev/null)"
echo "@binary $(stat -c %Y Config/bhuman 2>/dev/null)"
echo "@now $(date +%s)"
echo "@guard $([ -e "Config/Robots/$h/threads.cfg" ] && echo yes || echo no)"
echo "@led $(sed -n 's/^mode *= *\([A-Za-z]*\).*/\1/p' Config/Scenarios/Default/ledHandler.cfg 2>/dev/null | head -1)"
echo "@claim $(head -n 1 .bh-claim 2>/dev/null)"
echo "@backups $(ls .bh-backups/*.info 2>/dev/null | wc -l)"
for f in /var/volatile/tmp/ledState.txt /run/user/*/ledState.txt; do
  [ -r "$f" ] && { echo "@ledstate $(stat -c %Y "$f") $(head -n 1 "$f")"; break; }
done
echo "@model $(grep -o 'Booster [A-Z0-9]*' /opt/booster/robot_info.txt 2>/dev/null | head -1)"
# While the robot software is not running, nobody writes the battery level: read it from Booster's own
# battery topic instead. That takes about 3 s (Python and DDS discovery), so it runs in the background at
# most once a minute and this script reports the last reading.
bf="${XDG_RUNTIME_DIR:-/tmp}/bhBoosterBattery.txt"
if ! systemctl --user is-active -q bhuman.service 2>/dev/null && [ -d /opt/booster ]; then
  [ -e "$bf" ] && echo "@boosterbattery $(stat -c %Y "$bf") $(head -n 1 "$bf")"
  if [ $(( $(date +%s) - $(stat -c %Y "$bf.try" 2>/dev/null || echo 0) )) -gt 60 ]; then
    touch "$bf.try"
    FASTRTPS_DEFAULT_PROFILES_FILE=/opt/booster/BoosterRos2/fastdds_profile.xml nohup timeout 10 python3 -c '
import threading, booster_robotics_sdk_python as b
e = threading.Event(); r = []
def h(m):
    if not e.is_set(): r.append(m.soc); e.set()
b.ChannelFactory.Instance().Init(0); s = b.B1BatteryStateSubscriber(h); s.InitChannel()
if e.wait(5): open(__import__("sys").argv[1], "w").write("%.1f\n" % r[0])
' "$bf" </dev/null >/dev/null 2>&1 &
  fi
fi
f=$(ls -t logs/bhumand_*.log 2>/dev/null | head -1)
# a log cut off by a stop can end in zero bytes without a line break: drop them, keep lines short, and
# start a new line before "@end" so that the end marker is always found
[ -n "$f" ] && tail -n 6 "$f" | tr -d '\000' | cut -c1-300 | sed 's/^/@log /'
echo
echo "@end"
'''


def _num(text: str) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


# The values of LEDRequest::State (Src/Representations/Infrastructure/LEDRequest.h), in that order. The robot
# software writes the three states of the back LED (red, green, blue) as numbers; a test compares the order.
LED_STATES = ("off", "on", "blinking", "fastBlinking", "half", "halfBlinking", "halfFastBlinking")
LED_REPORT_MAX_AGE = 20            # the robot rewrites the file at least every 5 s while the software runs


def parse_led_report(parts: Sequence[str], robot_time: Optional[int]) -> Optional[dict]:
    """What the robot software last reported about its back LED: ``{"red": state, "green": state,
    "blue": state, "age_s": seconds}`` or None if there is no (valid) report."""
    if len(parts) != 4 or not all(p.isdigit() for p in parts) or any(int(p) >= len(LED_STATES) for p in parts[1:]):
        return None
    report: dict = {name: LED_STATES[int(p)] for name, p in zip(("red", "green", "blue"), parts[1:])}
    report["age_s"] = max(0, robot_time - int(parts[0])) if robot_time is not None else None
    return report


def parse_status(output: str) -> dict:
    """Turn the output of STATUS_SCRIPT into a dictionary."""
    s: dict = {"zones": [], "log": [], "complete": False}
    claim, led_report, booster_battery = "", [], []
    for line in output.splitlines():
        if not line.startswith("@"):
            continue
        key, _, value = line[1:].partition(" ")
        value = value.strip()
        if key == "end":
            s["complete"] = True
        elif key == "host":
            s["host"] = value
        elif key == "service":
            s["service"] = value or "unknown"
        elif key == "internal":
            parts = value.split()
            battery = _num(parts[0]) if parts else None
            s["battery"] = int(round(battery * 100)) if battery is not None and 0 <= battery <= 1 else None
            temp = _num(parts[1]) if len(parts) > 1 else None
            s["max_temp"] = int(temp) if temp is not None and temp > 0 else None
        elif key == "zone":
            milli = _num(value)
            if milli is not None:
                s["zones"].append(round(milli / 1000.0, 1))
        elif key == "disk":
            parts = value.split()
            free = _num(parts[0]) if parts else None
            s["disk_free_gb"] = round(free / 1048576.0, 1) if free is not None else None
            s["disk_used_pct"] = int(parts[1].rstrip("%")) if len(parts) > 1 and parts[1].rstrip("%").isdigit() else None
        elif key == "logs":
            s["logs"] = int(value) if value.isdigit() else 0
        elif key == "dump":
            s["dump"] = value == "yes"
        elif key == "newest":
            s["new_calibration"] = bool(value) and not value.endswith("settings.cfg")
        elif key == "settings":
            try:
                s["settings"] = core.parse_cfg(value)
            except BhError:
                s["settings"] = {}
        elif key == "wifi":
            parts = value.split()
            if len(parts) >= 3 and _num(parts[1]) is not None:
                s["wifi"] = {"device": parts[0], "quality": int(_num(parts[1])), "dbm": int(_num(parts[2]) or 0)}
        elif key == "load":
            s["load"] = [v for v in (_num(p) for p in value.split()) if v is not None]
        elif key == "mem":
            s["mem_pct"] = int(value) if value.isdigit() else None
        elif key == "uptime":
            s["uptime_s"] = int(value) if value.isdigit() else None
        elif key == "binary":
            s["binary_time"] = int(value) if value.isdigit() else None
        elif key == "now":
            s["robot_time"] = int(value) if value.isdigit() else None
        elif key == "guard":
            s["guard"] = value == "yes"
        elif key == "led":
            s["led"] = value or None
        elif key == "model":
            s["hardware"] = value or None
        elif key == "claim":
            claim = value
        elif key == "backups":
            s["backups"] = int(value) if value.isdigit() else 0
        elif key == "boosterbattery":
            booster_battery = value.split()
        elif key == "ledstate":
            led_report = value.split()
        elif key == "log":
            s["log"].append(value)
    # Both depend on the robot's clock, which is reported on another line.
    s["claim"] = share.parse_claim(claim, s.get("robot_time"))
    s["led_report"] = parse_led_report(led_report, s.get("robot_time"))
    # Booster's own reading, only used while the robot software reports none (it is not running).
    s["battery_source"] = "robot software" if s.get("battery") is not None else None
    if s.get("battery") is None and len(booster_battery) == 2 and booster_battery[0].isdigit():
        soc, now = _num(booster_battery[1]), s.get("robot_time")
        age = now - int(booster_battery[0]) if now is not None else None
        if soc is not None and 0 <= soc <= 100 and age is not None and age <= BOOSTER_BATTERY_MAX_AGE:
            s["battery"] = int(round(soc))
            s["battery_source"] = "Booster"
    return s


def collect(target: Target, timeout: float = 12) -> dict:
    """Gather the state of one robot. Never raises; see the "reachable" key."""
    base = {"name": target.robot.name if target.robot else target.ip, "ip": target.ip,
            "model": target.robot.model if target.robot else "", "time": time.time()}
    if not core.tcp_open(target.ip, timeout=1.0):
        base.update(reachable=False, error="not reachable")
        return base
    try:
        r = core.ssh_run(target.ip, STATUS_SCRIPT, timeout=timeout)
    except BhError as e:
        base.update(reachable=False, error=str(e))
        return base
    status = parse_status(r.out)
    if not status["complete"]:
        base.update(reachable=False, error=core.explain_ssh_failure(target, r))
        return base
    base.update(status, reachable=True, error=None)
    return base


def collect_all(targets: Sequence[Target]) -> List[dict]:
    if not targets:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
        return list(pool.map(collect, targets))


def assess(s: dict) -> List[Tuple[str, str]]:
    """Judge a status: list of (level, text) with level "ok", "warn" or "fail"."""
    out: List[Tuple[str, str]] = []
    if not s.get("reachable"):
        return [("fail", s.get("error") or "not reachable")]
    service = s.get("service", "unknown")
    if service == "active":
        out.append(("ok", "robot software is running"))
    elif service == "failed":
        out.append(("fail", "robot software crashed (bh logs %s)" % s["name"]))
    else:
        out.append(("warn", "robot software is not running (%s) - bh start %s" % (service, s["name"])))
    if s.get("dump"):
        out.append(("warn", "a crash dump from the last run exists (bh logs %s --crash)" % s["name"]))
    battery = s.get("battery")
    low = BATTERY_LOW.get(s.get("model", ""), 30)
    if battery is None:
        out.append(("warn", "battery level unknown (the robot software reports it while running)"))
    elif battery < low:
        out.append(("fail", "battery %d%% - below the %d%% the robot itself warns at" % (battery, low)))
    elif battery < low + 20:
        out.append(("warn", "battery %d%% - change it before a game" % battery))
    else:
        out.append(("ok", "battery %d%%" % battery))
    temp = s.get("max_temp")
    if temp is not None:
        if temp >= TEMP_VERY_HOT:
            out.append(("fail", "hottest joint %d C - let the robot cool down" % temp))
        elif temp >= TEMP_HOT:
            out.append(("warn", "hottest joint %d C" % temp))
        else:
            out.append(("ok", "hottest joint %d C" % temp))
    zones = s.get("zones") or []
    if zones and max(zones) >= CPU_HOT:
        out.append(("warn", "computer at %.0f C" % max(zones)))
    free = s.get("disk_free_gb")
    if free is not None and free < 2:
        out.append(("warn", "only %.1f GB free on the robot (bh logs %s --download --delete)" % (free, s["name"])))
    if s.get("new_calibration"):
        out.append(("warn", "calibration saved on the robot is not in the repository yet (bh calib pull %s)" % s["name"]))
    if s.get("guard"):
        out.append(("warn", "laptop guard is armed: the robot only moves while 'bh guard' is running"))
    return out


def describe(s: dict) -> List[str]:
    """One row for the status table."""
    if not s.get("reachable"):
        return [s["name"], s["ip"], "OFFLINE", "-", "-", "-", "-", "-", s.get("error") or ""]
    cfg = s.get("settings") or {}
    zones = s.get("zones") or []
    problems = [text for level, text in assess(s) if level != "ok"]
    claim = s.get("claim")
    return [s["name"], s["ip"], s.get("service", "?"),
            "%d%%" % s["battery"] if s.get("battery") is not None else "?",
            ("%d C" % s["max_temp"] if s.get("max_temp") is not None else "?")
            + (" / %.0f C" % max(zones) if zones else ""),
            "t%s p%s" % (cfg.get("teamNumber", "?"), cfg.get("playerNumber", "?")),
            "%s/%s" % (cfg.get("scenario", "?"), cfg.get("location", "?")),
            share.describe_claim(claim) if claim and not claim["expired"] else "-",
            "; ".join(problems) if problems else "ok"]


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_status(args) -> int:
    targets = core.resolve_targets(args.robots)
    stats = collect_all(targets)
    if args.json:
        say(json.dumps(stats, indent=2))
    else:
        say(core.table([describe(s) for s in stats],
                       ["robot", "address", "software", "battery", "joint / cpu", "team player", "scenario/location",
                        "in use by", "notes"]))
    return 0 if all(s.get("reachable") for s in stats) else 1


# On a K1 set up for the button routing, starting and stopping bhuman.service also restarts Booster's daemon
# (ExecStartPre / ExecStopPost, about 30 s; the unit allows 600 s). A start is only done when that is done.
# A stop is done - the robot is limp - as soon as the robot software has ended; the daemon restart after it is
# not waited for.
SERVICE_TIMEOUT = {"start": 150, "restart": 180, "stop": 30}


def service_script(verb: str) -> str:
    if verb == "stop":
        return ("systemctl --user stop --no-block %s\n"
                "for i in $(seq 60); do pgrep -x bhuman >/dev/null 2>&1 || exit 0; sleep 0.25; done\n"
                "echo 'the robot software is still running 15 s after the stop' >&2\n"
                "exit 3\n") % core.SERVICE
    return "systemctl --user %s %s\n" % (verb, core.SERVICE)


def run_service(t: Target, verb: str) -> core.Remote:
    return core.ssh_run(t.ip, service_script(verb), timeout=SERVICE_TIMEOUT[verb])


def _service(verb: str, targets: Sequence[Target]) -> int:
    def run(t: Target) -> Tuple[Target, core.Remote]:
        return t, run_service(t, verb)

    rc = 0
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(targets)))) as pool:
        for t, r in pool.map(run, targets):
            if r.ok:
                core.ok("%s: %s" % (t.label, {"stop": "stopped", "start": "started", "restart": "restarted"}[verb]))
            else:
                core.bad(core.explain_ssh_failure(t, r))
                rc = 1
    return rc


def cmd_stop(args) -> int:
    """Hard stop: the robot software ends and the robot goes limp at once."""
    targets = core.resolve_targets(args.robots, all_robots=args.all or not args.robots)
    say("Stopping the robot software - robots go limp and will fall if nobody holds them.")
    return _service("stop", targets)


def cmd_start(args) -> int:
    rc = _service("start", core.resolve_targets(args.robots, all_robots=args.all))
    say('Robots start limp. To stand one up: hold F1 for 2 s ("Body ready"), then STAND for 2 s, then WALK for 2 s.')
    return rc


def cmd_restart(args) -> int:
    targets = core.resolve_targets(args.robots, all_robots=args.all)
    if not args.yes and not core.confirm("Restarting makes the robots go limp for a moment. Are they sitting or held?"):
        return 1
    return _service("restart", targets)


def cmd_logs(args) -> int:
    t = core.resolve_target(args.robot)
    if args.download or args.delete:
        # Make/Common/downloadLogs stops a running robot software for the transfer and starts it again.
        if not core.confirm("The robot software of %s is stopped meanwhile - the robot goes limp. "
                            "Is it sitting or held?" % t.label, args.yes):
            return 1
        script_args = (["-d"] if args.download and args.delete else ["-D"] if args.delete else []) + [t.ip]
        return core.run_script("Make/Common/downloadLogs", script_args, check=False)
    if args.crash:
        return core.ssh_interactive(t.ip, "cat %s/bhdump.log 2>/dev/null || echo 'no crash dump on the robot'" % core.REMOTE_HOME)
    newest = "$(ls -t %s/logs/bhumand_*.log 2>/dev/null | head -1)" % core.REMOTE_HOME
    if args.follow:
        return core.ssh_interactive(t.ip, 'f="%s"; [ -n "$f" ] && tail -n %d -f "$f" || echo "no log yet"' % (newest, args.lines))
    r = core.ssh_run(t.ip, 'f="%s"; [ -n "$f" ] && { echo "== $f"; tail -n %d "$f"; } || echo "no log yet"' % (newest, args.lines))
    if not r.ok:
        raise BhError(core.explain_ssh_failure(t, r))
    say(r.out.rstrip())
    return 0


def cmd_ssh(args) -> int:
    t = core.resolve_target(args.robot)
    return core.ssh_interactive(t.ip, " ".join(args.command) if args.command else None)


def _power(verb: str, args) -> int:
    targets = core.resolve_targets(args.robots)
    names = ", ".join(t.label for t in targets)
    if not args.yes and not core.confirm("%s %s? Robots go limp - make sure they are sitting or held." % (verb.capitalize(), names)):
        return 1
    rc = 0
    for t in targets:
        # /bin/systemctl is the exact path the installer allows without a password.
        r = core.ssh_run(t.ip, "systemctl --user stop %s; sync; sudo -n /bin/systemctl %s" % (core.SERVICE, verb), timeout=20)
        # The connection usually drops while the robot goes down, which is fine.
        if r.ok or r.rc == 255 and not core.tcp_open(t.ip):
            core.ok("%s: %s requested" % (t.label, verb))
        else:
            core.bad(core.explain_ssh_failure(t, r))
            rc = 1
    return rc


def cmd_reboot(args) -> int:
    return _power("reboot", args)


def cmd_shutdown(args) -> int:
    return _power("poweroff", args)


def add_parsers(sub) -> None:
    p = sub.add_parser("status", help="battery, temperature, software state and settings of the robots")
    p.add_argument("robots", nargs="*", metavar="robot", help="names or addresses (default: all registered)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("stop", aliases=["estop"],
                       help="HARD STOP: end the robot software now (robots go limp). Default: all robots")
    p.add_argument("robots", nargs="*", metavar="robot")
    p.add_argument("--all", action="store_true", help="all registered robots (also the default without names)")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("start", help="start the robot software")
    p.add_argument("robots", nargs="*", metavar="robot")
    p.add_argument("--all", action="store_true", help="all registered robots")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("restart", help="restart the robot software")
    p.add_argument("robots", nargs="*", metavar="robot")
    p.add_argument("--all", action="store_true", help="all registered robots")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask")
    p.set_defaults(func=cmd_restart)

    p = sub.add_parser("logs", help="show, follow or download a robot's logs")
    p.add_argument("robot")
    p.add_argument("-n", "--lines", type=int, default=40, help="lines to show (default 40)")
    p.add_argument("-f", "--follow", action="store_true", help="keep printing new lines")
    p.add_argument("--crash", action="store_true", help="show the crash dump of the last run")
    p.add_argument("--download", action="store_true", help="download all log files into Config/Logs")
    p.add_argument("--delete", action="store_true", help="delete the log files on the robot (after --download)")
    p.add_argument("-y", "--yes", action="store_true",
                   help="do not ask before --download/--delete (they stop the robot software meanwhile)")
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("ssh", help="open a shell on a robot (or run one command)")
    p.add_argument("robot")
    p.add_argument("command", nargs="*")
    p.set_defaults(func=cmd_ssh)

    p = sub.add_parser("reboot", help="reboot robots (needed once after 'bh robot install')")
    p.add_argument("robots", nargs="+", metavar="robot")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_reboot)

    p = sub.add_parser("shutdown", help="power robots off cleanly")
    p.add_argument("robots", nargs="+", metavar="robot")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_shutdown)
