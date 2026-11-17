"""Sharing robots with other people: say who is using one, and put back what was on it.

Several people deploy to the same robots, and a deploy replaces the robot's software and its whole
configuration. Two small things make that workable:

  * a *claim* - a note on the robot saying who is using it, since when and for what. ``bh deploy`` and
    ``bh policy push`` refuse to touch a robot somebody else has claimed (``--ignore-claim`` overrides,
    ``bh stop`` never asks). A claim ends by itself after a few hours, so a forgotten one blocks nobody.
  * a *backup* - a snapshot, kept on the robot, of everything a deploy replaces (~/bin, ~/Config and the
    bhuman service files). ``bh robot restore`` puts it back, so the robot can be handed back the way it
    was found.

What a backup does not contain: system-wide settings (the vendor services ``bh robot install`` switches
off, files outside the home directory) and the logs.
"""

from __future__ import annotations

import getpass
import os
import re
import subprocess
from typing import Dict, List, Optional

from . import core
from .core import BhError, Target, say

CLAIM_FILE = ".bh-claim"            # in the home directory of user booster
CLAIM_HOURS = 8                     # a claim ends by itself after this long
BACKUP_DIR = ".bh-backups"          # in the home directory of user booster
BACKUP_RESERVE_KB = 1048576         # free space that must remain after a backup (1 GB)
BACKUP_ITEMS = ("bin", "Config", ".config/systemd/user/bhuman.service", ".config/systemd/user/bhuman.service.d")
BACKUP_ID = re.compile(r"^\d{8}-\d{6}$")
_UNSAFE = re.compile(r"[^A-Za-z0-9 _.@+-]")


def clean(text: str, limit: int) -> str:
    """Text that may be written into a file on the robot and embedded in a shell script: no quotes,
    no control characters."""
    return _UNSAFE.sub("", str(text or "")).strip()[:limit]


def me(given: Optional[str] = None) -> str:
    """The name other people see on the robot: --as, BH_USER, the git user name, or the login name."""
    candidates = [given, os.environ.get("BH_USER")]
    try:
        git = subprocess.run(["git", "config", "user.name"], cwd=str(core.ROOT), capture_output=True, text=True, timeout=10)
        candidates.append(git.stdout.strip() if git.returncode == 0 else None)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        candidates.append(getpass.getuser())
    except Exception:                       # getpass raises different things on different systems
        pass
    for candidate in candidates:
        name = clean(candidate or "", 40)
        if name:
            return name
    return "unknown"


# --------------------------------------------------------------------------
# Claims
# --------------------------------------------------------------------------

def parse_claim(line: str, robot_time: Optional[int]) -> Optional[dict]:
    """A claim as written by ``claim()``: who, until, since, note - separated by tabs, times as
    seconds of the *robot's* clock (robots without network time are far off; only differences count)."""
    parts = (line or "").rstrip("\r\n").split("\t")
    if len(parts) < 3 or not parts[0].strip() or not parts[1].isdigit() or not parts[2].isdigit():
        return None
    until, since = int(parts[1]), int(parts[2])
    claim = {"who": parts[0].strip(), "until": until, "since": since, "note": parts[3].strip() if len(parts) > 3 else ""}
    claim["expired"] = robot_time is not None and robot_time > until
    claim["age_s"] = max(0, robot_time - since) if robot_time is not None else None
    claim["left_s"] = max(0, until - robot_time) if robot_time is not None else None
    return claim


def read_claim(target: Target) -> Optional[dict]:
    r = core.ssh_run(target.ip, 'echo "@now $(date +%%s)"; echo "@claim $(head -n 1 "$HOME/%s" 2>/dev/null)"; echo "@end"'
                     % CLAIM_FILE, timeout=20)
    if "@end" not in r.out:
        raise BhError(core.explain_ssh_failure(target, r))
    now, line = None, ""
    for text in r.out.splitlines():
        if text.startswith("@now "):
            now = int(text[5:]) if text[5:].strip().isdigit() else None
        elif text.startswith("@claim "):
            line = text[7:]
    claim = parse_claim(line, now)
    if claim is not None:
        claim["robot_time"] = now
    return claim


def describe_claim(claim: dict) -> str:
    age = claim.get("age_s")
    since = "" if age is None else " %s ago" % _span(age)
    return "%s%s%s" % (claim["who"], since, " (%s)" % claim["note"] if claim.get("note") else "")


def _span(seconds: int) -> str:
    return "%d min" % max(1, seconds // 60) if seconds < 5400 else "%.1f h" % (seconds / 3600.0)


def foreign_claim(target: Target, who: str) -> Optional[dict]:
    """The claim that stands in the way of ``who``: somebody else's, and not yet expired."""
    claim = read_claim(target)
    if claim is None or claim["expired"] or claim["who"].lower() == who.lower():
        return None
    return claim


def respect_claim(target: Target, ignore: bool, who: Optional[str] = None) -> None:
    """Stop before changing a robot that somebody else is using."""
    claim = foreign_claim(target, who or me())
    if claim is None:
        return
    if ignore:
        core.warn("%s is marked as in use by %s - continuing anyway" % (target.label, describe_claim(claim)))
        return
    raise BhError("%s is in use by %s. Ask them first; then repeat with --ignore-claim, or let them release it "
                  "(bh robot release %s). The claim ends by itself in %s."
                  % (target.label, describe_claim(claim), target.robot.name if target.robot else target.ip,
                     _span(claim.get("left_s") or 0)))


def claim(target: Target, who: str, note: str, hours: float, force: bool) -> dict:
    current = read_claim(target)
    if current and not current["expired"] and current["who"].lower() != who.lower() and not force:
        raise BhError("%s is already in use by %s. Ask them, or take it over with --force."
                      % (target.label, describe_claim(current)))
    r = core.ssh_run(target.ip, 'echo "@now $(date +%s)"', timeout=20)
    now = next((int(l[5:]) for l in r.out.splitlines() if l.startswith("@now ") and l[5:].strip().isdigit()), None)
    if now is None:
        raise BhError(core.explain_ssh_failure(target, r))
    line = "%s\t%d\t%d\t%s\n" % (clean(who, 40), now + int(hours * 3600), now, clean(note, 80))
    core.ssh_write_file(target.ip, "%s/%s" % (core.REMOTE_HOME, CLAIM_FILE), line.encode("utf-8"))
    return parse_claim(line, now) or {}


def release(target: Target, who: str, force: bool) -> bool:
    """Remove the claim. Returns False if there was none."""
    current = read_claim(target)
    if current is None:
        return False
    if not current["expired"] and current["who"].lower() != who.lower() and not force:
        raise BhError("%s is in use by %s, not by you (%s). --force removes their claim."
                      % (target.label, describe_claim(current), who))
    r = core.ssh_run(target.ip, 'rm -f "$HOME/%s" && echo "@done"' % CLAIM_FILE, timeout=20)
    if "@done" not in r.out:
        raise BhError(core.explain_ssh_failure(target, r))
    return True


def cmd_claim(args) -> int:
    target = core.resolve_target(args.robot)
    if not 0 < args.hours <= 72:
        raise BhError("--hours must be between 0 and 72")
    who = me(args.who)
    made = claim(target, who, args.note or "", args.hours, args.force)
    core.ok("%s is now marked as in use by %s for %s%s" % (target.label, who, _span(int(args.hours * 3600)),
                                                             " (%s)" % made["note"] if made.get("note") else ""))
    say("Others see this in 'bh status' and the dashboard; their deploys ask them to talk to you first.")
    say("When you are done:  bh robot release %s" % args.robot)
    return 0


def cmd_release(args) -> int:
    target = core.resolve_target(args.robot)
    if release(target, me(args.who), args.force):
        core.ok("%s is free again" % target.label)
    else:
        core.ok("%s was not marked as in use" % target.label)
    return 0


# --------------------------------------------------------------------------
# Backups
# --------------------------------------------------------------------------

_ITEMS = " ".join(BACKUP_ITEMS)

BACKUP_SCRIPT = r'''
cd "$HOME" || { echo "@error no home directory"; exit 0; }
d="%(dir)s"; mkdir -p "$d"
items=""
for i in %(items)s; do [ -e "$i" ] && items="$items $i"; done
if [ -z "$items" ]; then echo "@error there is no robot software on this robot yet - nothing to back up"; exit 0; fi
need=$(du -sk $items 2>/dev/null | awk '{s+=$1} END {print s+0}')
free=$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')
if [ -n "$free" ] && [ $((need + %(reserve)d)) -gt "$free" ]; then
  echo "@error not enough space on the robot: the backup needs $((need / 1024)) MB, $((free / 1024)) MB are free (delete old ones: bh robot backups NAME --delete ID)"; exit 0
fi
id=$(date +%%Y%%m%%d-%%H%%M%%S)
while [ -e "$d/$id.tar" ]; do sleep 1; id=$(date +%%Y%%m%%d-%%H%%M%%S); done
enabled=$(systemctl --user is-enabled bhuman.service 2>/dev/null </dev/null)
low=""; command -v nice >/dev/null 2>&1 && low="nice -n 19"
if $low tar -cf "$d/$id.tar.part" $items 2>/dev/null </dev/null && mv "$d/$id.tar.part" "$d/$id.tar"; then
  printf '%%s\t%%s\t%%s\t%%s\t%%s\n' "$id" "$(date +%%s)" "$enabled" '%(who)s' '%(label)s' >"$d/$id.info"
  echo "@done $id $(du -k "$d/$id.tar" | cut -f1)"
else
  rm -f "$d/$id.tar.part"; echo "@error writing the backup failed"
fi
'''

LIST_SCRIPT = r'''
cd "$HOME/%(dir)s" 2>/dev/null || { echo "@end"; exit 0; }
echo "@now $(date +%%s)"
for f in *.info; do
  [ -f "$f" ] && [ -f "${f%%.info}.tar" ] && echo "@backup $(du -k "${f%%.info}.tar" | cut -f1)	$(head -n 1 "$f")"
done
echo "@end"
'''

RESTORE_SCRIPT = r'''
cd "$HOME" || { echo "@error no home directory"; exit 0; }
d="%(dir)s"; a="$d/%(id)s.tar"
[ -f "$a" ] || { echo "@error backup %(id)s does not exist on the robot"; exit 0; }
tar -tf "$a" >/dev/null 2>&1 </dev/null || { echo "@error backup %(id)s cannot be read (damaged file)"; exit 0; }
size=$(du -k "$a" | cut -f1)
free=$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')
if [ -n "$free" ] && [ $((size + 262144)) -gt "$free" ]; then
  echo "@error not enough space on the robot to restore safely ($((size / 1024)) MB needed, $((free / 1024)) MB free)"; exit 0
fi
systemctl --user stop bhuman.service </dev/null >/dev/null 2>&1
old="$d/replaced.$$"; rm -rf "$old"; mkdir -p "$old/.config/systemd/user"
for i in %(items)s; do [ -e "$i" ] && mv "$i" "$old/$i"; done
if tar -xf "$a" </dev/null 2>/dev/null; then
  rm -rf "$old"
else
  for i in %(items)s; do rm -rf "$i"; [ -e "$old/$i" ] && mv "$old/$i" "$i"; done
  rm -rf "$old"; echo "@error unpacking failed - the robot is as it was before"; exit 0
fi
systemctl --user daemon-reload </dev/null >/dev/null 2>&1
case "$(cut -f3 "$d/%(id)s.info" 2>/dev/null)" in
  enabled) systemctl --user enable bhuman.service </dev/null >/dev/null 2>&1;;
  disabled) systemctl --user disable bhuman.service </dev/null >/dev/null 2>&1;;
esac
echo "@done"
'''


def _result(target: Target, r: core.Remote) -> str:
    """The text after @done, or the script's own error as a BhError."""
    for line in r.out.splitlines():
        if line.startswith("@error "):
            raise BhError("%s: %s" % (target.label, line[7:]))
        if line.startswith("@done"):
            return line[5:].strip()
    raise BhError(core.explain_ssh_failure(target, r))


def backup(target: Target, label: str = "", who: Optional[str] = None) -> str:
    """Snapshot what a deploy would replace. Returns the id of the backup."""
    script = BACKUP_SCRIPT % {"dir": BACKUP_DIR, "items": _ITEMS, "reserve": BACKUP_RESERVE_KB,
                              "who": clean(who or me(), 40), "label": clean(label, 60)}
    done = _result(target, core.ssh_run(target.ip, script, timeout=900)).split()
    core.ok("%s: backup %s written on the robot (%d MB) - put it back with: bh robot restore %s %s"
            % (target.label, done[0], int(done[1]) // 1024 if len(done) > 1 and done[1].isdigit() else 0,
               target.robot.name if target.robot else target.ip, done[0]))
    return done[0]


def backups(target: Target) -> List[Dict[str, object]]:
    """The backups on the robot, newest first."""
    r = core.ssh_run(target.ip, LIST_SCRIPT % {"dir": BACKUP_DIR}, timeout=30)
    if "@end" not in r.out:
        raise BhError(core.explain_ssh_failure(target, r))
    now = next((int(l[5:]) for l in r.out.splitlines() if l.startswith("@now ") and l[5:].strip().isdigit()), None)
    found: List[Dict[str, object]] = []
    for line in r.out.splitlines():
        if not line.startswith("@backup "):
            continue
        parts = line[8:].split("\t")
        if len(parts) < 4 or not BACKUP_ID.match(parts[1]):
            continue
        made = int(parts[2]) if parts[2].isdigit() else None
        found.append({"id": parts[1], "mb": int(parts[0]) // 1024 if parts[0].isdigit() else 0,
                      "age_s": max(0, now - made) if now is not None and made is not None else None,
                      "service": parts[3], "who": parts[4] if len(parts) > 4 else "",
                      "label": parts[5].strip() if len(parts) > 5 else ""})
    return sorted(found, key=lambda b: str(b["id"]), reverse=True)


def restore(target: Target, backup_id: Optional[str] = None) -> str:
    """Put a backup back in place of what is on the robot now. The robot software is stopped first
    (the robot goes limp) and is NOT started again."""
    if backup_id is None:
        existing = backups(target)
        if not existing:
            raise BhError("%s has no backup (bh robot backup makes one)" % target.label)
        backup_id = str(existing[0]["id"])
    if not BACKUP_ID.match(backup_id):
        raise BhError("%r is not a backup id (they look like 20261117-140233; see bh robot backups)" % backup_id)
    _result(target, core.ssh_run(target.ip, RESTORE_SCRIPT % {"dir": BACKUP_DIR, "items": _ITEMS, "id": backup_id},
                                 timeout=900))
    core.ok("%s: backup %s is back in place; the robot software is stopped" % (target.label, backup_id))
    return backup_id


def delete_backup(target: Target, backup_id: str) -> None:
    if not BACKUP_ID.match(backup_id):
        raise BhError("%r is not a backup id" % backup_id)
    r = core.ssh_run(target.ip, 'cd "$HOME/%s" && rm -f "%s.tar" "%s.info" && echo "@done"'
                     % (BACKUP_DIR, backup_id, backup_id), timeout=60)
    _result(target, r)


def cmd_backup(args) -> int:
    target = core.resolve_target(args.robot)
    say("Copying what is on %s into a backup on the robot itself (this can take a minute)..." % target.label)
    backup(target, args.label or "", me(args.who))
    return 0


def cmd_backups(args) -> int:
    target = core.resolve_target(args.robot)
    if args.delete:
        delete_backup(target, args.delete)
        core.ok("%s: backup %s deleted" % (target.label, args.delete))
        return 0
    found = backups(target)
    if not found:
        say("%s has no backups. Make one with: bh robot backup %s" % (target.label, args.robot))
        return 0
    say(core.table([[b["id"], "%s MB" % b["mb"], _span(int(b["age_s"])) + " ago" if b["age_s"] is not None else "?",
                     b["who"], b["label"], b["service"] or "?"] for b in found],
                   ["backup", "size", "made", "by", "label", "service was"]))
    say("Put one back with:  bh robot restore %s <backup>" % args.robot)
    return 0


def cmd_restore(args) -> int:
    target = core.resolve_target(args.robot)
    respect_claim(target, args.ignore_claim, me(args.who))
    what = args.backup or "the newest backup"
    if not core.confirm("Restoring %s replaces the software and configuration that are on %s now and stops the "
                        "robot software - the robot goes limp. Is it sitting or held?" % (what, target.label), args.yes):
        return 1
    restore(target, args.backup)
    say("Start it with:  bh start %s" % args.robot)
    return 0


def add_actions(ps) -> None:
    """The sharing actions of 'bh robot'."""
    q = ps.add_parser("claim", help="mark a robot as in use by you, so that others do not deploy over your work")
    q.add_argument("robot")
    q.add_argument("--note", help="what you are doing, e.g. 'walk policy test'")
    q.add_argument("--hours", type=float, default=CLAIM_HOURS, help="the claim ends by itself after this long (default %d)" % CLAIM_HOURS)
    q.add_argument("--as", dest="who", help="your name as others should see it (default: git user name)")
    q.add_argument("--force", action="store_true", help="take over a robot somebody else has claimed")
    q.set_defaults(func=cmd_claim)

    q = ps.add_parser("release", help="say that you are done with a robot")
    q.add_argument("robot")
    q.add_argument("--as", dest="who", help="your name (default: git user name)")
    q.add_argument("--force", action="store_true", help="remove somebody else's claim")
    q.set_defaults(func=cmd_release)

    q = ps.add_parser("backup", help="snapshot what is on a robot (software, configuration, service) before you change it")
    q.add_argument("robot")
    q.add_argument("--label", help="a few words to recognise it by, e.g. 'Alex intercept build'")
    q.add_argument("--as", dest="who", help="your name (default: git user name)")
    q.set_defaults(func=cmd_backup)

    q = ps.add_parser("backups", help="list (or delete) the backups kept on a robot")
    q.add_argument("robot")
    q.add_argument("--delete", metavar="BACKUP", help="delete this backup")
    q.set_defaults(func=cmd_backups)

    q = ps.add_parser("restore", help="put a backup back (the robot software stops: the robot goes limp)")
    q.add_argument("robot")
    q.add_argument("backup", nargs="?", help="backup id from 'bh robot backups' (default: the newest)")
    q.add_argument("--as", dest="who", help="your name (default: git user name)")
    q.add_argument("--ignore-claim", action="store_true", help="also when somebody else has claimed the robot")
    q.add_argument("-y", "--yes", action="store_true", help="do not ask")
    q.set_defaults(func=cmd_restore)
