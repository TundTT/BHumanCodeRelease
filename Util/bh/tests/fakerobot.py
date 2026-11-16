"""Fake robots and a throw-away copy of the repository for tests.

Nothing here touches a real robot or the real checkout: ``ssh``, ``rsync``
and ``ping`` are replaced by stand-ins that operate on directory trees which
play the robots' file systems (one tree per address), and the repository
scripts run inside a temporary copy. This lets the tests drive the real
Install/ and Make/Common shell scripts end to end.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[3]

# ssh stand-in: "ssh [options] user@host [command]". The host selects the robot tree;
# an unknown host behaves like an unreachable robot (exit code 255, as ssh does).
FAKE_SSH = r'''
import os, re, subprocess, sys
robots = os.environ["FAKE_ROBOTS"]
bash = os.environ["FAKE_BASH"]
args = sys.argv[1:]
i = 0
while i < len(args) and args[i].startswith("-"):
    i += 2 if args[i] in ("-i", "-o", "-c", "-p") else 1
host = args[i].split("@", 1)[-1]
root = robots + "/" + host
command = " ".join(args[i + 1:])
if not os.path.isdir(root) or os.path.exists(root + "/offline"):
    sys.exit(255)
with open(root + "/ssh.log", "a") as f:
    f.write(command + "\n")
if not command:
    sys.exit(0)
MOUNTS = ("/home/booster", "/var/volatile/tmp", "/run/user", "/opt/booster", "/sys/devices/virtual/thermal")
def to_fake(text):
    # Paths that already point into the robot tree (results of earlier commands) stay as they are.
    # (Recognised by the directory name, because Git Bash spells the same directory differently.)
    for m in MOUNTS:
        text = re.sub("(?<!%s)%s" % (re.escape("/robots/" + host), re.escape(m)), lambda _: root + m, text)
    return text
home = root + "/home/booster"
env = dict(os.environ, HOME=home, USER="booster", FAKE_ROBOT_ROOT=root,
           PATH=root + "/bin" + os.pathsep + os.environ["PATH"])
if command.strip() == "bash -s":
    script = to_fake(sys.stdin.buffer.read().decode("utf-8")).encode("utf-8")
    p = subprocess.run([bash, "-s"], input=script, cwd=home, env=env, capture_output=True)
    # Scripts print the paths they looked at; show them as the robot would.
    sys.stdout.buffer.write(p.stdout.replace(root.encode("utf-8"), b""))
    sys.stderr.buffer.write(p.stderr.replace(root.encode("utf-8"), b""))
else:
    data = sys.stdin.buffer.read() if "cat >" in command else None
    p = subprocess.run([bash, "-c", to_fake(command)], input=data,
                       stdin=None if data is not None else subprocess.DEVNULL, cwd=home, env=env)
sys.exit(p.returncode)
'''

# rsync stand-in for "push local files to user@host:path" with --exclude and --del.
FAKE_RSYNC = r'''
import os, shutil, sys, fnmatch
robots = os.environ["FAKE_ROBOTS"]
args = sys.argv[1:]
excludes, delete, positional = [], False, []
i = 0
while i < len(args):
    a = args[i]
    if a.startswith("--exclude="):
        excludes.append(a.split("=", 1)[1])
    elif a in ("--del", "--delete"):
        delete = True
    elif a.startswith("--"):
        pass
    elif a.startswith("-"):
        if a.endswith("e"):
            i += 1
    else:
        positional.append(a)
    i += 1
sources, dest = positional[:-1], positional[-1]
assert "@" in dest and ":" in dest, "fake rsync only pushes to a robot"
host, path = dest.split("@", 1)[1].split(":", 1)
root = robots + "/" + host
if not os.path.isdir(root) or os.path.exists(root + "/offline") or os.path.exists(root + "/rsync-fails"):
    sys.stderr.write("rsync: connection unexpectedly closed\n")
    sys.exit(12)
dest = root + path
os.makedirs(dest, exist_ok=True)
def excluded(rel):
    parts = rel.split("/")
    for n in range(1, len(parts) + 1):
        sub = "/".join(parts[:n])
        for e in excludes:
            if e.startswith("/"):
                if sub == e[1:]:
                    return True
            elif fnmatch.fnmatch(parts[n - 1], e):
                return True
    return False
sent = set()
def send(src, rel):
    if excluded(rel):
        return
    sent.add(rel)
    if os.path.isdir(src):
        os.makedirs(os.path.join(dest, rel), exist_ok=True)
        for n in os.listdir(src):
            send(os.path.join(src, n), rel + "/" + n)
    else:
        os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
        shutil.copyfile(src, os.path.join(dest, rel))
for s in sources:
    if s.endswith("/."):
        for n in os.listdir(s[:-2]):
            send(os.path.join(s[:-2], n), n)
    else:
        send(s, os.path.basename(s.rstrip("/")))
if delete:
    # Like rsync: what was not sent is removed, except what an exclude protects.
    for dirpath, dirs, files in os.walk(dest, topdown=False):
        for n in files + dirs:
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, dest).replace(os.sep, "/")
            if rel in sent or excluded(rel):
                continue
            if os.path.isdir(full) and not os.path.islink(full):
                if not os.listdir(full):
                    os.rmdir(full)
            else:
                os.remove(full)
'''

SYSTEMCTL = r'''#!/bin/bash
state="$FAKE_ROBOT_ROOT/service.state"
[ "$1" = "--user" ] && shift
case "$1" in
  is-active) cat "$state" 2>/dev/null || echo inactive; [ "$(cat "$state" 2>/dev/null)" = active ];;
  is-failed) echo inactive; exit 1;;
  is-enabled) cat "$FAKE_ROBOT_ROOT/service.enabled" 2>/dev/null || echo enabled;;
  start|restart) echo active >"$state";;
  stop) echo inactive >"$state";;
  list-unit-files) cat "$FAKE_ROBOT_ROOT/units.enabled" 2>/dev/null;;
  list-units) cat "$FAKE_ROBOT_ROOT/units.running" 2>/dev/null;;
  disable|enable) echo "$@" >>"$FAKE_ROBOT_ROOT/systemctl.log";;
esac
'''

CRONTAB = r'''#!/bin/bash
f="$FAKE_ROBOT_ROOT/crontab"
case "$1" in
  -l) [ -f "$f" ] && cat "$f" || exit 1;;
  -r) rm -f "$f";;
  *) cp "$1" "$f";;
esac
'''


def _script(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.chmod(path, 0o755)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def find_bash(tmp: Path) -> Optional[str]:
    """A POSIX bash that can run the repository scripts and that looks up
    commands in PATH order, so that the stand-ins win (on Windows that is
    Git's usr/bin/bash.exe; its bin/bash.exe wrapper puts /usr/bin first)."""
    probe = tmp / "probe"
    _script(probe / "hostname", "#!/bin/bash\necho stand-in\n")
    candidates = [os.environ.get("BH_TEST_BASH")]
    if os.name == "nt":
        candidates += [r"C:\Program Files\Git\usr\bin\bash.exe"]
    candidates.append(shutil.which("bash"))
    env = dict(os.environ, PATH=str(probe) + os.pathsep + os.environ["PATH"])
    for c in candidates:
        if not c or not os.path.exists(c) or "system32" in c.lower():
            continue  # System32\bash.exe is the WSL launcher, not a shell for these files
        try:
            if subprocess.run([c, "-c", "hostname"], capture_output=True, timeout=30, env=env).stdout.strip() == b"stand-in":
                return c.replace("\\", "/")
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def make_repo(tmp: Path) -> Path:
    """Copy the parts of the repository the tools and scripts need."""
    root = tmp / "repo"
    shutil.copytree(REPO / "Make" / "Common", root / "Make" / "Common")
    shutil.copytree(REPO / "Install", root / "Install")
    big = shutil.ignore_patterns("Scenes", "Sounds", "Logs", "Images", "RobotBallPerceptor", "TensorRTCache",
                                 "BallAndPenaltyMarkPerceptor", "FieldBoundary", "IntersectionsClassifier",
                                 "WhistleDetector", "Odometry", ".*")
    shutil.copytree(REPO / "Config", root / "Config", ignore=big)
    # The tests register their own robots.
    for d in (root / "Config" / "Robots").iterdir():
        if (d / "network.cfg").is_file():
            shutil.rmtree(d)
    _write(root / "Config" / "Robots" / "robots.cfg",
           "//WARNING: Do not edit this file manually, use the script createRobot instead!\nrobotsIds = [\n];\n")
    (root / "Config" / "Scenes" / "Includes").mkdir(parents=True)
    _write(root / "Util" / "libduerwen" / "lib" / "LinuxArm64" / "libduerwen_wakeup_DOA_v1.1.0_20251113.so", "lib")
    _write(root / "Util" / "X5Camera" / "update_x5", "#!/bin/sh\n")
    _write(root / "Build" / "Linux" / "Booster" / "Develop" / "bhuman", "fake robot binary (Develop)\n")
    (root / "Src").mkdir()
    return root


def make_robot(tmp: Path, name: str, ip: str, volatile: bool = False) -> Path:
    """Create the directory tree that stands in for the robot at ``ip``.

    The default is a stock NVIDIA Jetson Ubuntu as found on robots that were not set up by Install/: there
    is no /var/volatile, only /run/user/1000 (the home of the user's systemd session). ``volatile=True``
    gives the layout the installer creates (/var/volatile/tmp)."""
    root = tmp / "robots" / ip
    home = root / "home" / "booster"
    for stub in ("setprofile", "setvolume", "bhuman"):
        _script(home / "bin" / stub, "#!/bin/bash\necho \"$(basename \"$0\") $*\" >>\"$FAKE_ROBOT_ROOT/bin.log\"\n")
    _write(home / "logs" / "bhumand_(1).log", "12:00:00 Saying Hello\n12:00:01 Saying Low battery\n")
    _write(home / "Config" / "settings.cfg",
           "teamNumber = 89;\nplayerNumber = 3;\nlocation = Default;\nscenario = Default;\n")
    capi = home / ".local" / "lib" / "python3.10" / "site-packages" / "onnxruntime" / "capi"
    for lib in ("libonnxruntime.so.1.22.0", "libonnxruntime_providers_tensorrt.so", "libonnxruntime_providers_shared.so"):
        _write(capi / lib, "lib")
    if volatile:
        _write(root / "var" / "volatile" / "tmp" / "internalState.txt", "0.83 47")
    else:
        _write(root / "run" / "user" / "1000" / "internalState.txt", "0.83 47")
        _write(root / "etc" / "nv_tegra_release", "# R36 (release), REVISION: 4.3\n")
    _write(root / "opt" / "booster" / "robot_info.txt", "Booster K1\nfirmware 1.7.1\n")
    _write(root / "opt" / "booster" / "config" / "containerd" / "config.yaml",
           "- name: ros.camera\n  enable: true\n- name: motion\n  enable: true\n- name: lui\n  enable: false\n")
    _write(root / "sys" / "devices" / "virtual" / "thermal" / "thermal_zone0" / "temp", "51500\n")
    _write(root / "units.enabled", "bhuman.service enabled\npulseaudio.service enabled\nevilbot.service enabled\n")
    _write(root / "units.running", "pulseaudio.service loaded active running Sound\n")
    _write(root / "crontab", "# comment\n@reboot /home/booster/old_team/start.sh\n")
    _script(root / "bin" / "hostname", "#!/bin/bash\necho %s\n" % name)
    _script(root / "bin" / "systemctl", SYSTEMCTL)
    _script(root / "bin" / "crontab", CRONTAB)
    _script(root / "bin" / "sudo", '#!/bin/bash\n[ "$1" = "-n" ] && shift\n[ "$1" = "-l" ] && { [ -e "$FAKE_ROBOT_ROOT/nosudo" ] && exit 1; exit 0; }\nexec "$@"\n')
    _script(root / "bin" / "id", '#!/bin/bash\n[ "$1" = "-u" ] && echo 1000 || echo "uid=1000(booster)"\n')
    return root


def make_local_tools(tmp: Path) -> Path:
    """Stand-ins for ssh/rsync/ping that the repository's bash scripts find on PATH."""
    tools = tmp / "tools"
    py = sys.executable.replace("\\", "/")
    _write(tools / "fake_ssh.py", FAKE_SSH)
    _write(tools / "fake_rsync.py", FAKE_RSYNC)
    # MSYS (Git Bash) would rewrite arguments that look like paths before they reach Python.
    launcher = '#!/bin/bash\nexport MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"\nexec "%s" "%s" "$@"\n'
    _script(tools / "ssh", launcher % (py, (tools / "fake_ssh.py").as_posix()))
    _script(tools / "rsync", launcher % (py, (tools / "fake_rsync.py").as_posix()))
    _script(tools / "ping", "#!/bin/bash\nexit 0\n")
    return tools
