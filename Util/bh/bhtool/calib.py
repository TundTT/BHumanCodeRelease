"""Calibration files: see what differs between robot and repository, pull them.

A calibration is done *on the robot* (see Docs/calibration.md) and saved
there as Config/Robots/<name>/cameraCalibration.cfg / imuCalibration.cfg.
It only survives the next deploy if it is copied back into the repository.
``bh deploy`` does that automatically; these commands make it visible.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Dict, List, Tuple

from . import core
from .core import BhError, Target, say

FILES = ("cameraCalibration.cfg", "imuCalibration.cfg")
MANUAL_FILES = ("jointCalibration.cfg",)

REMOTE_SCRIPT = r'''
cd /home/booster 2>/dev/null
h=$(hostname)
echo "@host $h"
echo "@newest $(ls -t Config/settings.cfg Config/Robots/$h/cameraCalibration.cfg Config/Robots/$h/imuCalibration.cfg 2>/dev/null | head -1)"
for n in cameraCalibration.cfg imuCalibration.cfg jointCalibration.cfg; do
  f="Config/Robots/$h/$n"
  [ -f "$f" ] && echo "@file $n $(sha256sum "$f" | cut -d' ' -f1) $(stat -c %Y "$f")"
done
echo "@end"
'''


def remote_state(target: Target) -> Tuple[str, bool, Dict[str, Tuple[str, int]]]:
    """(hostname, calibration saved since last deploy, {file: (sha256, mtime)})."""
    r = core.ssh_run(target.ip, REMOTE_SCRIPT, timeout=20)
    if "@end" not in r.out:
        raise BhError(core.explain_ssh_failure(target, r))
    host, newest, files = "", "", {}
    for line in r.out.splitlines():
        parts = line.split()
        if line.startswith("@host") and len(parts) > 1:
            host = parts[1]
        elif line.startswith("@newest") and len(parts) > 1:
            newest = parts[1]
        elif line.startswith("@file") and len(parts) == 4:
            files[parts[1]] = (parts[2], int(parts[3]))
    return host, bool(newest) and not newest.endswith("settings.cfg"), files


def local_dir(name: str) -> Path:
    return core.config_dir() / "Robots" / name


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pull(target: Target) -> List[str]:
    """Copy the robot's calibration files into the repository. Returns the changed file names."""
    host, _, files = remote_state(target)
    name = target.robot.name if target.robot else host
    if not name:
        raise BhError("could not determine the robot's name")
    changed = []
    for f in FILES:
        if f not in files:
            continue
        local = local_dir(name) / f
        if local.is_file() and _sha(local) == files[f][0]:
            continue
        data = core.ssh_read_file(target.ip, "%s/Robots/%s/%s" % (core.REMOTE_CONFIG, host, f))
        if local.is_file():
            backup = local.parent / ".calibration-backup"
            backup.mkdir(exist_ok=True)
            (backup / ("%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), f))).write_bytes(local.read_bytes())
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        changed.append(f)
    # Same as Make/Common/downloadCalibration: mark the robot's calibration as "downloaded".
    core.ssh_run(target.ip, "touch %s/settings.cfg" % core.REMOTE_CONFIG, timeout=10)
    return changed


def cmd_status(args) -> int:
    rows = []
    for t in core.resolve_targets(args.robots):
        name = t.robot.name if t.robot else t.ip
        local = {f: (local_dir(name) / f).is_file() for f in FILES + MANUAL_FILES}
        try:
            _, saved, files = remote_state(t)
        except BhError as e:
            rows.append([name, "?", "?", "?", str(e).splitlines()[0][:60]])
            continue
        cells = []
        for f in FILES + MANUAL_FILES:
            if f in files and local[f]:
                cells.append("same" if _sha(local_dir(name) / f) == files[f][0] else "DIFFERENT")
            elif f in files:
                cells.append("robot only")
            elif local[f]:
                cells.append("repo only")
            else:
                cells.append("default")
        note = "saved on the robot, not pulled yet -> bh calib pull %s" % name if saved else ""
        if cells[0] == "default" and not note:
            note = "camera never calibrated for this robot (Docs/calibration.md)"
        rows.append([name] + cells + [note])
    say(core.table(rows, ["robot", "camera", "imu", "joints", "notes"]))
    say("default = no robot-specific file, the values from Config/Robots/Default are used.")
    return 0


def cmd_pull(args) -> int:
    for t in core.resolve_targets(args.robots):
        changed = pull(t)
        if changed:
            core.ok("%s: pulled %s into Config/Robots/%s (commit them!)"
                    % (t.label, ", ".join(changed), t.robot.name if t.robot else "?"))
        else:
            core.ok("%s: repository already has the robot's calibration" % t.label)
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("calib", help="compare and pull robot calibration files (see Docs/calibration.md)")
    ps = p.add_subparsers(dest="calib_cmd", metavar="<action>")
    ps.required = True
    q = ps.add_parser("status", help="which calibration files exist and whether robot and repository agree")
    q.add_argument("robots", nargs="*", metavar="robot")
    q.set_defaults(func=cmd_status)
    q = ps.add_parser("pull", help="copy calibration saved on the robot into Config/Robots/<name>")
    q.add_argument("robots", nargs="+", metavar="robot")
    q.set_defaults(func=cmd_pull)
