"""Robot onboarding: register, inspect (scan), install, quarantine foreign software.

The actual installation is done by the proven scripts in Install/. This
module adds what was missing around them: a read-only inspection of what is
already on a robot, a reversible way to switch off other people's autostart
software, and one guided command that chains the steps.
"""

from __future__ import annotations

import re
from typing import Dict, List

from . import core
from .core import BhError, Target, say

# Services of the stock Booster software that Install/Files/install switches off
# because they compete with this code for camera, GPU, audio or buttons.
BOOSTER_SERVICES_TO_DISABLE = {
    "ros.booster.video_stream", "ros.booster.vision_detection", "ros.booster.auto_handeye_calib",
    "server.perception", "ros.camera", "lui", "audio", "battery_daemon", "booster_camera",
}

# User-level units that belong to the operating system session, not to anybody's robot code.
SYSTEM_USER_UNITS = re.compile(
    r"^(bhuman|pulseaudio|pipewire.*|wireplumber|dbus.*|gvfs.*|gnome.*|xdg.*|at-spi.*|evolution.*|tracker.*|"
    r"snap.*|ssh-agent|gpg-agent.*|dconf.*|dirmngr|systemd-.*|obex|gcr-.*|ibus.*|unity.*|update-notifier.*|"
    r"indicator-.*|im-config|keyboxd|session-migration|speech-dispatcher|filter-chain|xfce.*|lxqt.*)(@.*)?\.service$")

# Processes that are expected to use CPU on a healthy robot.
KNOWN_PROCESSES = re.compile(
    r"^(bhuman|systemd.*|sshd.*|bash|sh|ps|awk|sed|head|kworker.*|ksoftirqd.*|rcu_.*|irq/.*|nv.*|"
    r"NetworkManager|wpa_supplicant|pulseaudio|pipewire.*|dbus-daemon|containerd.*|booster.*|device_gateway|"
    r"cam_stream|Xorg|gnome-.*|gdm.*|snapd|cron|rsyslogd|journald|udevd|polkitd|avahi-daemon|thermald|"
    r"multipathd|irqbalance|nvpmodel|jetson.*|tegra.*|mawk|tee)$")

UNIT_NAME = re.compile(r"^[A-Za-z0-9@._:-]+\.service$")
ROBOT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,30}$")

SCAN_SCRIPT = r'''
echo "@host $(hostname)"
echo "@info $(tr '\n' ';' 2>/dev/null </opt/booster/robot_info.txt)"
echo "@fw $(awk '/^Version:/ {v=$2} END {print v}' /opt/booster/version.txt 2>/dev/null)"
echo "@kernel $(uname -r) $(uname -m)"
echo "@disk $(df -Pk /home/booster 2>/dev/null | awk 'NR==2 {print $4}')"
echo "@bhuman $([ -x /home/booster/bin/bhuman ] && echo yes || echo no) $([ -f /home/booster/Config/bhuman ] && echo yes || echo no) $(systemctl --user is-enabled bhuman.service 2>/dev/null || echo no)"
echo "@ort $(ls /home/booster/.local/lib/python*/site-packages/onnxruntime/capi/libonnxruntime.so.* 2>/dev/null | tail -1)"
echo "@ortproviders $(ls /home/booster/.local/lib/python*/site-packages/onnxruntime/capi/ 2>/dev/null | grep -o 'providers_[a-z]*' | sort -u | tr '\n' ' ')"
systemctl --user list-unit-files --type=service --state=enabled --no-legend 2>/dev/null </dev/null | awk '{print "@uenabled " $1}'
systemctl --user list-units --type=service --state=running --no-legend --plain 2>/dev/null </dev/null | awk '{print "@urunning " $1}'
systemctl list-units --type=service --state=running --no-legend --plain 2>/dev/null </dev/null | awk '{print "@srunning " $1}'
crontab -l 2>/dev/null </dev/null | grep -v '^[[:space:]]*#' | grep -v '^[[:space:]]*$' | sed 's/^/@cron /'
{ cat /opt/booster/config/containerd/config.yaml 2>/dev/null || sudo -n cat /opt/booster/config/containerd/config.yaml 2>/dev/null; } </dev/null \
  | awk '/name:/ {n=$NF} /enable:/ {print "@booster " n, $NF}'
ps -eo pcpu,user,comm --sort=-pcpu --no-headers 2>/dev/null </dev/null | head -15 | sed 's/^/@proc /'
if command -v docker >/dev/null 2>&1; then docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null </dev/null | sed 's/^/@docker /'; fi
ls -1 /home/booster/.config/autostart 2>/dev/null | sed 's/^/@autostart /'
echo "@svcactive $(systemctl --user is-active bhuman.service 2>/dev/null </dev/null)"
echo "@svcenabled $(systemctl --user is-enabled bhuman.service 2>/dev/null </dev/null)"
echo "@svcfailed $(systemctl --user is-failed bhuman.service 2>/dev/null </dev/null)"
for f in /home/booster/.config/systemd/user/bhuman.service.d/*.conf; do
  [ -f "$f" ] && { echo "@dropin $f"; grep -E '^(Exec[A-Za-z]*|Environment|Timeout[A-Za-z]*|Restart)=' "$f" 2>/dev/null | sed "s|^|@dropinline $f |"; }
done
for d in /var/volatile/tmp "/run/user/$(id -u)"; do
  if [ -d "$d" ] && [ -w "$d" ]; then echo "@statedir $d yes"; else echo "@statedir $d no"; fi
done
for d in /home/booster/logs /home/booster/Config; do
  if [ ! -d "$d" ]; then echo "@dir $d missing"; elif [ -w "$d" ]; then echo "@dir $d yes"; else echo "@dir $d no"; fi
done
if sudo -n -l /bin/systemctl </dev/null >/dev/null 2>&1; then echo "@sudo yes"; else echo "@sudo no"; fi
echo "@campkg $(dpkg-query -W -f='${Version}' booster-x5-camera 2>/dev/null </dev/null)"
f=$(ls -t /home/booster/logs/bhumand_*.log 2>/dev/null </dev/null | head -1)
if [ -n "$f" ]; then
  echo "@lognew $f"
  tail -n 20000 "$f" 2>/dev/null </dev/null | awk '{k=$0; gsub(/[0-9]+/, "N", k); c[k]++; if (!(k in s)) s[k]=$0} END {for (k in c) if (c[k] >= 200) print c[k], s[k]}' | sort -rn | head -5 | sed 's/^/@logrep /'
  echo "@logcalib $(tail -n 20000 "$f" 2>/dev/null </dev/null | grep -c 'calibration received')"
fi
echo "@end"
'''


def parse_scan(output: str) -> Dict[str, List[str]]:
    data: Dict[str, List[str]] = {}
    for line in output.splitlines():
        if line.startswith("@"):
            key, _, value = line[1:].partition(" ")
            data.setdefault(key, []).append(value.strip())
    return data


def analyse_scan(data: Dict[str, List[str]]) -> dict:
    """Classify what a scan found. Pure function, easy to test."""
    first = lambda key: (data.get(key) or [""])[0]
    bh = (first("bhuman").split() + ["no", "no", "no"])[:3]
    foreign_units = sorted({u for u in data.get("uenabled", []) + data.get("urunning", [])
                            if UNIT_NAME.match(u) and not SYSTEM_USER_UNITS.match(u)})
    booster_on = sorted(name for name, state in (line.split()[:2] for line in data.get("booster", [])
                                                 if len(line.split()) >= 2)
                        if name in BOOSTER_SERVICES_TO_DISABLE and state.lower() == "true")
    busy = []
    for line in data.get("proc", []):
        parts = line.split(None, 2)
        if len(parts) == 3:
            try:
                cpu = float(parts[0])
            except ValueError:
                continue
            if cpu >= 5.0 and not KNOWN_PROCESSES.match(parts[2]):
                busy.append("%s (%s, %.0f%% CPU)" % (parts[2], parts[1], cpu))
    info = first("info")
    return {
        "host": first("host"),
        "info": [p for p in info.split(";") if p.strip()],
        "installed": bh[0] == "yes",
        "deployed": bh[1] == "yes",
        "service_enabled": bh[2] == "enabled",
        "onnxruntime": first("ort"),
        "ort_providers": first("ortproviders").split(),
        "foreign_units": foreign_units,
        "cron": data.get("cron", []),
        "booster_services_on": booster_on,
        "busy_processes": busy,
        "docker": data.get("docker", []),
        "autostart": data.get("autostart", []),
        "service_active": first("svcactive"),
        "service_failed": first("svcfailed") == "failed",
        "service_enabled_state": first("svcenabled"),
        "dropins": _dropins(data),
        "state_dirs": {p: v == "yes" for p, v in (line.rsplit(None, 1) for line in data.get("statedir", []) if " " in line)},
        "dirs": {p: v for p, v in (line.rsplit(None, 1) for line in data.get("dir", []) if " " in line)},
        "sudo_systemctl": first("sudo") == "yes",
        "camera_package": first("campkg"),
        "newest_log": first("lognew"),
        "log_repeats": _log_repeats(data.get("logrep", [])),
        "log_calibrations": int(first("logcalib") or 0) if first("logcalib").isdigit() else 0,
        "firmware_installed": first("fw"),
        "complete": "end" in data,
    }


def _dropins(data: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Drop-in files that change bhuman.service -> the lines in them that matter (Exec..., Timeout...)."""
    result: Dict[str, List[str]] = {f: [] for f in data.get("dropin", [])}
    for line in data.get("dropinline", []):
        name, _, text = line.partition(" ")
        result.setdefault(name, []).append(text.strip())
    return result


def _log_repeats(lines: List[str]) -> List[tuple]:
    """(count, example line) of the lines that fill the log: '829000 [cam_stream_receiver] ...'."""
    found = []
    for line in lines:
        count, _, text = line.partition(" ")
        if count.isdigit():
            found.append((int(count), text.strip()))
    return found


TESTED_FIRMWARE = "1.7.1"
STATE_DIR_HINT = ("the robot software writes its battery/temperature file to /var/volatile/tmp (created by the "
                  "installer) or /run/user/<uid>")
CAMERA_LOOP = re.compile(r"cam_stream_receiver.*(resync|unexpected calib)", re.I)
CAMERA_RELATED = re.compile(r"cam_stream_receiver", re.I)


def firmware_version(info: List[str], installed: str = "") -> str:
    """The robot firmware version: the last 'Version:' entry of /opt/booster/version.txt (the list of every
    firmware the robot ever had, e.g. '1.8.1.2-release-02884-2026-10-15-global'), else a firmware line of
    /opt/booster/robot_info.txt ('' if neither names one)."""
    found = re.search(r"\d+\.\d+\.\d+(?:\.\d+)?", installed)
    if found:
        return found.group()
    for line in info:
        # "Model Version: 1.0.1" is the version of the robot model, not of the firmware
        if re.search(r"firmware|software|version", line, re.I) and not re.search(r"model", line, re.I):
            found = re.search(r"\d+\.\d+\.\d+(?:\.\d+)?", line)
            if found:
                return found.group()
    return ""


def will_it_run(s: dict) -> List[tuple]:
    """Can this code run on the robot as it is? (level, text) with level 'ok', 'warn' or 'fail'.
    The scan before looked at what is installed; this looks at what the software needs while it runs
    (writable places, a service that starts, a camera that delivers, the firmware). Pure function."""
    checks: List[tuple] = []
    add = lambda level, text: checks.append((level, text))

    if not s["installed"]:
        add("fail", "the start scripts are not installed (bh robot install)")
    elif not s["deployed"]:
        add("warn", "nothing is deployed yet (bh deploy)")
    else:
        add("ok", "software is on the robot (~/bin/bhuman, ~/Config/bhuman)")

    if s["installed"]:
        if s["service_failed"]:
            add("fail", "bhuman.service has failed - look at 'systemctl --user status bhuman' and the newest log")
        enabled = s["service_enabled_state"]
        if enabled != "enabled":
            add("warn", "bhuman.service is %s: after a reboot or a battery change the software stays off "
                        "(systemctl --user enable bhuman)" % (enabled or "not known to systemd"))
        else:
            add("ok", "bhuman.service starts by itself after a reboot (now: %s)" % (s["service_active"] or "unknown"))
    for name, lines in sorted(s["dropins"].items()):
        add("warn", "bhuman.service is changed by %s: %s" % (name, "; ".join(lines) or "(no Exec/Timeout lines)"))
        if any("sudo" in line for line in lines) and not s["sudo_systemctl"]:
            add("fail", "%s runs sudo before or after every start, but 'sudo -n' is not allowed here" % name)

    dirs = s["state_dirs"]
    if not dirs:
        pass
    elif any(dirs.values()):
        add("ok", "status file can be written (%s)" % ", ".join(sorted(p for p, w in dirs.items() if w)))
    else:
        add("warn", "no writable directory for the status file (%s); none of %s can be written, so bh status "
                    "and the dashboard show the battery as unknown" % (STATE_DIR_HINT, ", ".join(sorted(dirs))))
    for path, state in sorted(s["dirs"].items()):
        if state == "no":
            add("fail", "%s is not writable by user booster" % path)
        elif state == "missing":
            add("warn", "%s does not exist yet" % path)

    loops = [(n, t) for n, t in s["log_repeats"] if CAMERA_LOOP.search(t)]
    # the reconnects that belong to a reported loop are part of it, not further findings
    others = [(n, t) for n, t in s["log_repeats"] if not CAMERA_LOOP.search(t) and not (loops and CAMERA_RELATED.search(t))]
    for n, text in loops:
        add("fail", "camera stream is stuck in a loop: %d times in the last 20000 log lines (with the reconnects "
                    "after it): %s - the software "
                    "gets no calibration and no images that way. The receiver (Util/X5Camera/include/"
                    "cam_stream_receiver.cpp) and the program on the camera board must come from the same "
                    "version%s" % (n, text, " (camera package %s)" % s["camera_package"] if s["camera_package"] else ""))
    for n, text in others[:3]:
        add("warn", "a line fills the log (%d times in the last 20000 lines): %s" % (n, text))
    if s["newest_log"] and not s["log_repeats"]:
        add("ok", "no repeating errors in the newest log (%s)" % s["newest_log"].rsplit("/", 1)[-1])
    elif not s["newest_log"] and s["installed"]:
        add("ok", "no log yet (the software has not run)")
    if s["newest_log"] and not loops and s["log_calibrations"]:
        add("ok", "camera calibration was received (%d time(s) in the newest log)" % s["log_calibrations"])
    if s["camera_package"]:
        add("ok", "camera package booster-x5-camera %s" % s["camera_package"])

    version = firmware_version(s["info"], s["firmware_installed"])
    if not version:
        add("warn", "firmware version not found in /opt/booster/version.txt or robot_info.txt (tested: %s)" % TESTED_FIRMWARE)
    elif version == TESTED_FIRMWARE or version.startswith(TESTED_FIRMWARE + "."):
        add("ok", "firmware %s is the tested one" % version)
    elif version.startswith("1.8.0"):
        add("warn", "firmware %s is known to misbehave with this code (tested: %s)" % (version, TESTED_FIRMWARE))
    else:
        add("warn", "firmware %s has not been tested with this code (tested: %s)" % (version, TESTED_FIRMWARE))

    if not s["sudo_systemctl"]:
        add("warn", "'sudo -n /bin/systemctl' is not allowed: bh reboot, bh shutdown and the installer need it")
    return checks


def verdict(checks: List[tuple]) -> str:
    failed = sum(1 for level, _ in checks if level == "fail")
    warned = sum(1 for level, _ in checks if level == "warn")
    if failed:
        return "VERDICT: the software will NOT run properly (%d failed, %d warnings)" % (failed, warned)
    if warned:
        return "VERDICT: should run, but check the %d warning(s) above" % warned
    return "VERDICT: ready to run"


def scan(target: Target, password: bool = False) -> dict:
    r = core.ssh_run(target.ip, SCAN_SCRIPT, timeout=120 if password else 40, batch=not password)
    result = analyse_scan(parse_scan(r.out))
    if not result["complete"]:
        raise BhError(core.explain_ssh_failure(target, r)
                      + ("" if password else "\nFor a robot without the team key use: bh robot scan <address> --password"))
    return result


def print_scan(target: Target, s: dict) -> int:
    """Print the findings; returns the number of things that need attention."""
    issues = 0
    say("Robot at %s (hostname %s)" % (target.ip, s["host"] or "?"))
    for line in s["info"]:
        say("    " + line.strip())
    if s["installed"]:
        core.ok("this code's start scripts are installed" + (", software deployed" if s["deployed"] else ", nothing deployed yet"))
    else:
        core.warn("not set up for this code yet -> bh robot install <name>")
        issues += 1
    if s["booster_services_on"]:
        core.warn("stock Booster services still enabled that compete for camera/GPU/audio: %s\n"
                  "         'bh robot install' switches them off (a backup of the vendor configuration is kept)"
                  % ", ".join(s["booster_services_on"]))
        issues += 1
    elif s["installed"]:
        core.ok("competing stock Booster services are switched off")
    if s["onnxruntime"]:
        providers = s["ort_providers"]
        if "providers_tensorrt" in providers or "providers_cuda" in providers:
            core.ok("ONNX Runtime with GPU support found on the robot")
        else:
            core.warn("ONNX Runtime found but without TensorRT/CUDA providers - vision would run slowly")
            issues += 1
    else:
        core.warn("ONNX Runtime is missing in ~/.local of user booster - deploying will stop with "
                  "'ONNXruntime not found' (see Docs/robot-onboarding.md)")
        issues += 1
    if s["foreign_units"]:
        core.warn("autostart services that are not part of this code: %s" % ", ".join(s["foreign_units"]))
        say("         switch them off (reversible):  bh robot quarantine %s %s"
            % (target.robot.name if target.robot else target.ip, " ".join("--unit " + u for u in s["foreign_units"])))
        issues += 1
    if s["cron"]:
        core.warn("scheduled jobs (crontab) of user booster:")
        for line in s["cron"]:
            say("           " + line)
        say("         switch them off (reversible):  bh robot quarantine %s --cron"
            % (target.robot.name if target.robot else target.ip))
        issues += 1
    if s["autostart"]:
        core.warn("desktop autostart entries: %s (inactive once the installer disables the desktop on K1)"
                  % ", ".join(s["autostart"]))
    if s["docker"]:
        core.warn("running containers: %s - stop them if they are not needed, they share CPU and GPU"
                  % "; ".join(s["docker"]))
        issues += 1
    if s["busy_processes"]:
        core.warn("unknown processes using the CPU right now: %s" % "; ".join(s["busy_processes"]))
        issues += 1
    if not issues:
        core.ok("nothing found that would interfere with this code")
    say("Will it run? (what the software needs while it runs)")
    checks = will_it_run(s)
    for level, text in checks:
        {"ok": core.ok, "warn": core.warn, "fail": core.bad}[level](text)
    say(verdict(checks))
    return issues + sum(1 for level, _ in checks if level != "ok")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_list(args) -> int:
    robots = core.load_robots()
    if not robots:
        say("No robots registered yet. Add one:  bh robot add <name> --id <last number of its address>")
        return 0
    rows = []
    for r in robots:
        via = "cable" if core.tcp_open(r.lan) else "wifi" if core.tcp_open(r.wlan) else "offline"
        rows.append([r.name, r.model.upper() or "?", r.lan, r.wlan, via])
    say(core.table(rows, ["robot", "type", "cable address", "wifi address", "reachable"]))
    return 0


def _team_number(args) -> str:
    return str(args.team) if getattr(args, "team", None) is not None else core.settings().get("teamNumber", "")


def add_robot(name: str, robot_id: int, model: str, team: str) -> None:
    if not ROBOT_NAME.match(name):
        raise BhError("robot names start with a letter and use only letters, digits, '-' and '_'")
    if not 1 <= robot_id <= 254:
        raise BhError("--id must be between 1 and 254 (it becomes the last number of the robot's address)")
    if core.find_robot(name):
        raise BhError("robot %s already exists (bh robot list)" % name)
    for r in core.load_robots():
        if r.lan.endswith(".%s.%d" % (team, robot_id)):
            raise BhError("address number %d is already used by %s" % (robot_id, r.name))
    core.run_script("Install/createRobot", ["-t", team, "-r", str(robot_id), "-m", model, name])
    r = core.find_robot(name)
    if r is None:
        raise BhError("Install/createRobot did not create Config/Robots/%s" % name)
    say("Registered %s (%s): cable %s, wifi %s" % (r.name, r.model.upper(), r.lan, r.wlan))


def cmd_add(args) -> int:
    add_robot(args.name, args.id, args.model, _team_number(args))
    say("Next:  bh robot install %s   (robot switched on, connected by cable at its factory address %s)"
        % (args.name, core.FACTORY_IP))
    return 0


def cmd_remove(args) -> int:
    r = core.find_robot(args.name)
    if r is None:
        raise BhError("unknown robot %s" % args.name)
    if not args.yes and not core.confirm("Delete Config/Robots/%s including its calibration files?" % r.name):
        return 1
    core.run_script("Install/createRobot", ["-D", r.name])
    return 0


def _scan_target(spec: str) -> Target:
    if spec.lower() == "factory":
        return Target(core.FACTORY_IP, None)
    return core.resolve_target(spec)


def cmd_scan(args) -> int:
    target = _scan_target(args.robot)
    if args.password:
        say("You may be asked for the password of user 'booster' on the robot.")
    issues = print_scan(target, scan(target, args.password))
    return 1 if issues else 0


def install_robot(name: str, mode: str = "install") -> None:
    r = core.find_robot(name)
    if r is None:
        raise BhError("unknown robot %s - register it first: bh robot add %s --id <n>" % (name, name))
    flags = {"install": [], "update": ["--update"], "uninstall": ["--delete"], "uninstall-all": ["--deleteAll"]}[mode]
    if mode == "install":
        say("Installing on the robot at its factory address %s (connect by cable; your computer needs an "
            "address like 192.168.10.x). You will be asked for the password of user 'booster'." % core.FACTORY_IP)
    core.run_script("Install/installRobot", flags + [r.name])


def cmd_install(args) -> int:
    mode = "update" if args.update else "uninstall" if args.uninstall else "uninstall-all" if args.uninstall_all else "install"
    install_robot(args.name, mode)
    say("Done. The robot must be rebooted once:  bh reboot %s" % args.name)
    return 0


def cmd_quarantine(args) -> int:
    target = _scan_target(args.robot)
    if args.undo:
        script = r'''
q="$HOME/.bh-quarantine"
if [ -f "$q/units" ]; then
  while read -r u; do [ -n "$u" ] && systemctl --user enable --now "$u" </dev/null && echo "re-enabled $u"; done <"$q/units"
  rm -f "$q/units"
fi
latest=$(ls -t "$q"/crontab.* 2>/dev/null | head -1)
if [ -n "$latest" ]; then crontab "$latest" </dev/null && echo "restored crontab" && rm -f "$latest"; fi
echo "@done"
'''
    else:
        units = list(args.unit or [])
        if args.all:
            found = scan(target)
            units += [u for u in found["foreign_units"] if u not in units]
        for u in units:
            if not UNIT_NAME.match(u):
                raise BhError("%r is not a service name (expected something like name.service)" % u)
            if SYSTEM_USER_UNITS.match(u):
                raise BhError("%s belongs to the system or to this code and must stay enabled" % u)
        if not units and not args.cron:
            raise BhError("nothing to do: name services with --unit, or use --all (everything 'bh robot scan' "
                          "flags) and/or --cron")
        say("On %s this will:" % target.label)
        for u in units:
            say("    stop and disable the user service %s" % u)
        if args.cron:
            say("    save and remove the crontab of user booster")
        say("    (undo any time with: bh robot quarantine %s --undo)" % args.robot)
        if args.dry_run:
            return 0
        if not args.yes and not core.confirm("Proceed?"):
            return 1
        script = 'q="$HOME/.bh-quarantine"; mkdir -p "$q"\n'
        for u in units:
            script += ('systemctl --user disable --now %s </dev/null && { echo %s >>"$q/units"; echo "disabled %s"; }\n'
                       % (u, u, u))
        script += '[ -f "$q/units" ] && sort -u -o "$q/units" "$q/units"\n'
        if args.cron:
            script += ('if crontab -l >/dev/null 2>&1 </dev/null; then crontab -l >"$q/crontab.$(date +%s)" </dev/null '
                       '&& crontab -r </dev/null && echo "crontab saved and removed"; fi\n')
        script += 'echo "@done"\n'
    r = core.ssh_run(target.ip, script, timeout=60)
    if "@done" not in r.out:
        raise BhError(core.explain_ssh_failure(target, r))
    for line in r.out.splitlines():
        if not line.startswith("@"):
            say("  " + line)
    if r.err.strip():
        core.warn(r.err.strip())
    return 0


def cmd_onboard(args) -> int:
    """Guided path from a factory-fresh (or foreign) robot to one that runs this code."""
    name = args.name
    say("== Step 1/4: register %s on this computer" % name)
    if core.find_robot(name):
        core.ok("%s is already registered" % name)
    else:
        if args.id is None:
            raise BhError("--id is needed for a new robot (the last number of its address, unique per robot)")
        add_robot(name, args.id, args.model, _team_number(args))
    say("== Step 2/4: look at what is on the robot now (read-only)")
    factory = Target(core.FACTORY_IP, None)
    if not core.tcp_open(core.FACTORY_IP, timeout=2):
        raise BhError("no robot answers at the factory address %s. Connect the robot by cable, switch it on and "
                      "give your computer an address like 192.168.10.50 (netmask 255.255.255.0)." % core.FACTORY_IP)
    try:
        say("You may be asked for the password of user 'booster'.")
        print_scan(factory, scan(factory, password=True))
    except BhError as e:
        core.warn("could not inspect the robot: %s" % e)
    say("== Step 3/4: install (switches off competing vendor services, sets name and addresses)")
    if not args.yes and not core.confirm("Install this code's environment on the robot now?"):
        say("Stopped. Continue later with: bh robot install %s" % name)
        return 1
    install_robot(name)
    say("== Step 4/4: reboot")
    r = core.ssh_run(core.FACTORY_IP, "sync; sudo -n /bin/systemctl reboot", timeout=20)
    if r.ok or r.rc == 255:
        core.ok("reboot requested")
    else:
        core.warn("please reboot the robot by hand (power button)")
    say("")
    say("When the robot is back (about a minute):")
    say("    bh robot scan %s        check that nothing foreign is left" % name)
    say("    bh deploy %s            build and copy the software" % name)
    say("    Docs/calibration.md     calibrate the new robot before it plays")
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("robot", help="register, inspect and set up robots (new or with other software on them)",
                       description="Manage the robots this code is deployed to.")
    ps = p.add_subparsers(dest="robot_cmd", metavar="<action>")
    ps.required = True

    q = ps.add_parser("list", help="registered robots and whether they are reachable")
    q.set_defaults(func=cmd_list)

    q = ps.add_parser("onboard", help="guided setup of a factory-fresh or foreign robot (register, scan, install, reboot)")
    q.add_argument("name")
    q.add_argument("--id", type=int, help="last number of the robot's address (1-254, unique per robot)")
    q.add_argument("--model", choices=["k1", "t1"], default="k1")
    q.add_argument("--team", type=int, help="team number (default: Config/settings.cfg)")
    q.add_argument("-y", "--yes", action="store_true", help="do not ask before installing")
    q.set_defaults(func=cmd_onboard)

    q = ps.add_parser("add", help="register a robot on this computer (creates Config/Robots/<name>)")
    q.add_argument("name")
    q.add_argument("--id", type=int, required=True, help="last number of the robot's address (1-254)")
    q.add_argument("--model", choices=["k1", "t1"], default="k1")
    q.add_argument("--team", type=int, help="team number (default: Config/settings.cfg)")
    q.set_defaults(func=cmd_add)

    q = ps.add_parser("remove", help="forget a robot (deletes Config/Robots/<name>)")
    q.add_argument("name")
    q.add_argument("-y", "--yes", action="store_true")
    q.set_defaults(func=cmd_remove)

    q = ps.add_parser("scan", help="read-only check of a robot for software that would interfere")
    q.add_argument("robot", help="robot name, address, or 'factory' for %s" % core.FACTORY_IP)
    q.add_argument("--password", action="store_true", help="log in with the robot's password (no team key installed yet)")
    q.set_defaults(func=cmd_scan)

    q = ps.add_parser("install", help="prepare a robot for this code (runs Install/installRobot)")
    q.add_argument("name")
    g = q.add_mutually_exclusive_group()
    g.add_argument("--update", action="store_true", help="re-run on an already installed K1 at its own address")
    g.add_argument("--uninstall", action="store_true", help="restore the vendor setup (keeps this code's files)")
    g.add_argument("--uninstall-all", action="store_true", help="restore the vendor setup and delete this code's files")
    q.set_defaults(func=cmd_install)

    q = ps.add_parser("quarantine", help="reversibly switch off foreign autostart services / cron jobs on a robot")
    q.add_argument("robot")
    q.add_argument("--unit", action="append", metavar="NAME.service", help="user service to stop and disable (repeatable)")
    q.add_argument("--all", action="store_true", help="every foreign user service that 'scan' reports")
    q.add_argument("--cron", action="store_true", help="save and remove the crontab of user booster")
    q.add_argument("--undo", action="store_true", help="re-enable everything that was quarantined")
    q.add_argument("--dry-run", action="store_true", help="only show what would be done")
    q.add_argument("-y", "--yes", action="store_true")
    q.set_defaults(func=cmd_quarantine)
