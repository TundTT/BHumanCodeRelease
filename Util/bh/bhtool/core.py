"""Shared helpers: repository paths, .cfg parsing, the robot registry and
process/ssh plumbing. Everything else in bhtool builds on this module."""

from __future__ import annotations

import getpass
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

# Util/bh/bhtool/core.py -> repository root is three levels above this package.
ROOT = Path(os.environ.get("BH_ROOT") or Path(__file__).resolve().parents[3])

SSH_USER = "booster"
REMOTE_HOME = "/home/booster"
REMOTE_CONFIG = REMOTE_HOME + "/Config"
SERVICE = "bhuman.service"
BUILD_CONFIGS = ("Debug", "Develop", "Release")
FACTORY_IP = "192.168.10.102"
MODELS = {"k1": "K1", "t1": "T1"}


class BhError(Exception):
    """A problem the user can act on. The CLI prints it without a traceback."""


# --------------------------------------------------------------------------
# Output (ASCII only: Windows consoles may not be able to encode more)
# --------------------------------------------------------------------------

_output = threading.local()


def capture_output(sink: Optional[Callable[[str], None]]) -> None:
    """Send everything this thread prints with say() - and the output of the scripts it runs -
    to ``sink`` (one call per line) instead of the terminal. ``None`` switches back. The
    dashboard uses this to show a running deploy in the browser."""
    _output.sink = sink


def output_sink() -> Optional[Callable[[str], None]]:
    return getattr(_output, "sink", None)


def say(msg: str = "") -> None:
    sink = output_sink()
    if sink is not None:
        for line in str(msg).split("\n"):
            sink(line)
    else:
        print(msg, flush=True)


def _call(cmd: Sequence[str], cwd: str) -> int:
    """Run a program with inherited stdio, or line by line through the output sink of this thread."""
    sink = output_sink()
    if sink is None:
        return subprocess.call(list(cmd), cwd=cwd)
    p = subprocess.Popen(list(cmd), cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert p.stdout is not None
    for raw in iter(p.stdout.readline, b""):
        sink(raw.decode("utf-8", "replace").rstrip("\r\n"))
    p.stdout.close()
    return p.wait()


def ok(msg: str) -> None:
    say("  [ ok ] " + msg)


def warn(msg: str) -> None:
    say("  [warn] " + msg)


def bad(msg: str) -> None:
    say("  [FAIL] " + msg)


def note(msg: str) -> None:
    say("  [info] " + msg)


def confirm(question: str, assume_yes: bool = False) -> bool:
    """Ask a yes/no question. Without a terminal the answer is always no - also in a dashboard job,
    whose output goes to the page (the page asks before it starts the job)."""
    if assume_yes:
        return True
    if output_sink() is not None or not sys.stdin or not sys.stdin.isatty():
        return False
    try:
        return input(question + " [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        say()
        return False


def table(rows: Sequence[Sequence[object]], header: Optional[Sequence[str]] = None) -> str:
    """Format rows as a left-aligned text table."""
    data = [[str(c) for c in row] for row in rows]
    if header:
        data.insert(0, list(header))
    if not data:
        return ""
    widths = [max(len(row[i]) for row in data if i < len(row)) for i in range(max(len(r) for r in data))]
    lines = []
    for n, row in enumerate(data):
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
        if header and n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Platform
# --------------------------------------------------------------------------

def is_windows() -> bool:
    return os.name == "nt"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_wsl() -> bool:
    if is_windows() or is_macos():
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def host_os() -> str:
    """Name of the Build/<OS> directory that belongs to this host."""
    if is_windows():
        return "Windows"
    if is_macos():
        return "macOS"
    return "Linux"


# --------------------------------------------------------------------------
# B-Human .cfg files
# --------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""(?P<ws>\s+)
      | (?P<lc>//[^\n]*)
      | (?P<bc>/\*.*?\*/)
      | (?P<str>"(?:[^"\\]|\\.)*")
      | (?P<punct>[=;,\[\]{}])
      | (?P<bare>[^\s=;,\[\]{}"]+)""",
    re.X | re.S,
)


class _Tok(NamedTuple):
    kind: str   # "str", "punct" or "bare"
    text: str
    start: int
    end: int


def _tokenize(text: str) -> List[_Tok]:
    tokens = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise BhError("cannot parse configuration near: %r" % text[pos:pos + 30])
        kind = m.lastgroup
        if kind in ("str", "punct", "bare"):
            tokens.append(_Tok(kind, m.group(), m.start(), m.end()))
        pos = m.end()
    return tokens


class _Parser:
    """Recursive-descent parser for the ``key = value;`` configuration format.

    Structures become dicts, lists become lists and every scalar becomes a
    string (quotes removed). ``spans`` maps the dotted path of each scalar to
    its character range so that single values can be replaced in place.
    """

    def __init__(self, text: str):
        self.text = text
        self.tokens = _tokenize(text)
        self.i = 0
        self.spans: Dict[str, Tuple[int, int, bool]] = {}

    def peek(self) -> Optional[_Tok]:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self, expected: Optional[str] = None) -> _Tok:
        tok = self.peek()
        if tok is None:
            raise BhError("unexpected end of configuration (expected %r)" % (expected or "a value"))
        if expected is not None and tok.text != expected:
            raise BhError("expected %r but found %r" % (expected, tok.text))
        self.i += 1
        return tok

    def body(self, path: str, closer: Optional[str]) -> dict:
        result: dict = {}
        while True:
            tok = self.peek()
            if tok is None:
                if closer is None:
                    return result
                raise BhError("missing %r in configuration" % closer)
            if closer is not None and tok.text == closer:
                self.i += 1
                return result
            key = self.take()
            if key.kind != "bare":
                raise BhError("expected an attribute name but found %r" % key.text)
            self.take("=")
            child = key.text if not path else path + "." + key.text
            result[key.text] = self.value(child)
            self.take(";")

    def value(self, path: str):
        tok = self.take()
        if tok.text == "{":
            return self.body(path, "}")
        if tok.text == "[":
            items = []
            index = 0
            while True:
                nxt = self.peek()
                if nxt is None:
                    raise BhError("missing ']' in configuration")
                if nxt.text == "]":
                    self.i += 1
                    return items
                items.append(self.value("%s[%d]" % (path, index)))
                index += 1
                nxt = self.peek()
                if nxt is not None and nxt.text == ",":
                    self.i += 1
        if tok.kind == "punct":
            raise BhError("unexpected %r in configuration" % tok.text)
        # A scalar may be an expression of several tokens ("xPosGoal - 1000").
        last = tok
        while self.peek() is not None and self.peek().kind != "punct":
            last = self.take()
        quoted = tok.kind == "str" and last is tok
        self.spans[path] = (tok.start, last.end, quoted)
        return tok.text[1:-1] if quoted else self.text[tok.start:last.end]


def parse_cfg(text: str) -> dict:
    """Parse the text of a .cfg file into nested dicts/lists of strings."""
    return _Parser(text).body("", None)


def read_cfg(path: Path) -> dict:
    try:
        return parse_cfg(path.read_text(encoding="utf-8", errors="replace"))
    except BhError as e:
        raise BhError("%s: %s" % (path, e))


def read_exact(path: Path) -> str:
    """Read a text file without translating line endings."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def write_exact(path: Path, text: str) -> None:
    """Write a text file exactly as given (the repository uses LF everywhere)."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def cfg_get(data, dotted: str, default=None):
    """Look up ``a.b.c`` in a parsed configuration."""
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def cfg_replace(text: str, dotted: str, new_value: str) -> str:
    """Return ``text`` with the scalar at ``dotted`` replaced, keeping all
    comments and formatting. Raises BhError if the attribute does not exist."""
    parser = _Parser(text)
    parser.body("", None)
    if dotted not in parser.spans:
        raise BhError("attribute %r not found" % dotted)
    start, end, quoted = parser.spans[dotted]
    return text[:start] + ('"%s"' % new_value if quoted else new_value) + text[end:]


# --------------------------------------------------------------------------
# Settings and the configuration search path
# --------------------------------------------------------------------------

def config_dir() -> Path:
    return ROOT / "Config"


def settings() -> dict:
    return read_cfg(config_dir() / "settings.cfg")


def search_path(model: str, robot: str = "Default", scenario: str = "Default",
                location: str = "Default") -> List[Path]:
    """Directories searched for a configuration file, first match wins.
    Mirrors Settings::updateSearchPath in Src/Libs/Framework/Settings.cpp."""
    c = config_dir()
    t = MODELS.get(model.lower(), model)
    dirs = [c / "Robots" / robot]
    if location != "Default":
        dirs += [c / "Locations" / location / t, c / "Locations" / location]
    if scenario != "Default":
        dirs += [c / "Scenarios" / scenario / t, c / "Scenarios" / scenario]
    dirs += [c / "Robots" / "Default" / t, c / "Robots" / "Default",
             c / "Locations" / "Default" / t, c / "Locations" / "Default",
             c / "Scenarios" / "Default" / t, c / "Scenarios" / "Default", c]
    return dirs


def find_config(name: str, model: str, robot: str = "Default", scenario: str = "Default",
                location: str = "Default") -> Optional[Path]:
    for d in search_path(model, robot, scenario, location):
        if (d / name).is_file():
            return d / name
    return None


def list_names(kind: str) -> List[str]:
    """Names of the directories in Config/<kind> (Scenarios, Locations)."""
    base = config_dir() / kind
    return sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []


# --------------------------------------------------------------------------
# Robot registry (Config/Robots/<name>/network.cfg + Config/Robots/robots.cfg)
# --------------------------------------------------------------------------

class Robot(NamedTuple):
    name: str
    lan: str
    wlan: str
    model: str  # "k1", "t1" or "" if unknown


_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def is_ip(text: str) -> bool:
    return bool(_IPV4.match(text)) and all(0 <= int(p) <= 255 for p in text.split("."))


def load_robots() -> List[Robot]:
    base = config_dir() / "Robots"
    models: Dict[str, str] = {}
    robots_file = base / "robots.cfg"
    if robots_file.is_file():
        for entry in read_cfg(robots_file).get("robotsIds", []):
            if isinstance(entry, dict) and "name" in entry:
                models[entry["name"]] = str(entry.get("robotType", "")).lower()
    result = []
    if base.is_dir():
        for d in sorted(base.iterdir()):
            net = d / "network.cfg"
            if net.is_file():
                cfg = read_cfg(net)
                name = cfg.get("name", d.name)
                result.append(Robot(name, cfg.get("lan", ""), cfg.get("wlan", ""), models.get(name, "")))
    return result


def find_robot(spec: str) -> Optional[Robot]:
    """Find a registered robot by name (case-insensitive) or by one of its IPs."""
    for r in load_robots():
        if spec.lower() == r.name.lower() or spec in (r.lan, r.wlan):
            return r
    return None


def tcp_open(ip: str, port: int = 22, timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


class Target(NamedTuple):
    ip: str
    robot: Optional[Robot]

    @property
    def label(self) -> str:
        return "%s (%s)" % (self.robot.name, self.ip) if self.robot else self.ip


def resolve_target(spec: str, prefer: Optional[str] = None, probe: bool = True) -> Target:
    """Turn a robot name or IP address into a Target.

    For a name, the cabled address is used if it answers on the ssh port,
    otherwise the wireless one. ``prefer`` ("lan" or "wlan") skips the probe.
    """
    if is_ip(spec):
        return Target(spec, find_robot(spec))
    robot = find_robot(spec)
    if robot is None:
        known = ", ".join(r.name for r in load_robots()) or "none yet - add one with: bh robot add"
        raise BhError("unknown robot %r (registered: %s)" % (spec, known))
    if prefer == "lan":
        return Target(robot.lan, robot)
    if prefer == "wlan":
        return Target(robot.wlan, robot)
    if probe:
        for ip in (robot.lan, robot.wlan):
            if ip and tcp_open(ip):
                return Target(ip, robot)
    return Target(robot.lan or robot.wlan, robot)


def resolve_targets(specs: Sequence[str], all_robots: bool = False, prefer: Optional[str] = None) -> List[Target]:
    if all_robots or not specs:
        robots = load_robots()
        if not robots:
            raise BhError("no robots registered yet - add one with: bh robot add <name> --id <n>")
        specs = [r.name for r in robots]
    return [resolve_target(s, prefer) for s in specs]


# --------------------------------------------------------------------------
# Running repository shell scripts
# --------------------------------------------------------------------------

def split_command(text: str) -> List[str]:
    """Split a command line from an environment variable; quotes group words
    and backslashes stay literal (Windows paths)."""
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    lexer.commenters = ""
    return list(lexer)


def _wsl_available() -> bool:
    exe = shutil.which("wsl.exe")
    if not exe:
        return False
    try:
        out = subprocess.run([exe, "-l", "-q"], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    # wsl.exe prints UTF-16; any distribution name means it is usable.
    return out.returncode == 0 and bool(out.stdout.replace(b"\x00", b"").strip())


def bash_prefix() -> List[str]:
    """Command prefix that runs ``bash`` with the repository as working directory.

    On Windows the build and deploy scripts need a Linux userland (rsync,
    clang), i.e. WSL. BH_BASH overrides the detection, e.g. for tests.
    """
    override = os.environ.get("BH_BASH")
    if override:
        return split_command(override)
    if is_windows():
        if not _wsl_available():
            raise BhError("this step needs WSL (Windows Subsystem for Linux), which is not set up. "
                          "Run 'bh doctor' for the setup steps.")
        return ["wsl.exe", "--cd", str(ROOT), "--exec", "bash"]
    if not shutil.which("bash"):
        raise BhError("bash not found")
    return ["bash"]


def run_script(script: str, args: Sequence[str] = (), check: bool = True) -> int:
    """Run a repository script (path relative to the root) with inherited stdio."""
    if not (ROOT / script).is_file():
        raise BhError("script %s is missing" % script)
    cmd = bash_prefix() + [script] + [str(a) for a in args]
    rc = _call(cmd, str(ROOT))
    if check and rc != 0:
        raise BhError("%s failed (exit code %d)" % (script, rc))
    return rc


def run_bash(command: str, check: bool = True) -> int:
    """Run a bash command line in the repository root with inherited stdio."""
    rc = _call(bash_prefix() + ["-c", command], str(ROOT))
    if check and rc != 0:
        raise BhError("command failed (exit code %d): %s" % (rc, command))
    return rc


# --------------------------------------------------------------------------
# ssh
# --------------------------------------------------------------------------

def ssh_key() -> Path:
    """Private copy of the team's ssh key with permissions ssh accepts."""
    src = ROOT / "Install" / "Keys" / "id_rsa_b-human"
    if not src.is_file():
        raise BhError("ssh key %s is missing" % src)
    dst_dir = Path.home() / ".bh"
    dst = dst_dir / "id_rsa_b-human"
    data = src.read_bytes()
    if not dst.is_file() or dst.read_bytes() != data:
        dst_dir.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            try:
                os.chmod(dst, 0o600)
            except OSError:
                pass
        dst.write_bytes(data)
        if is_windows():
            # OpenSSH for Windows refuses keys that other accounts can read.
            subprocess.run(["icacls", str(dst), "/inheritance:r", "/grant:r", "%s:F" % getpass.getuser()],
                           capture_output=True)
        else:
            os.chmod(dst, 0o600)
    return dst


def ssh_command(ip: str, batch: bool = True, connect_timeout: int = 5, user: str = SSH_USER,
                tty: bool = False) -> List[str]:
    override = os.environ.get("BH_SSH")
    if override:
        base = split_command(override)
    else:
        exe = shutil.which("ssh")
        if not exe:
            raise BhError("ssh client not found. Install OpenSSH (Windows: Settings > Optional features > OpenSSH Client).")
        base = [exe]
    cmd = base + ["-i", str(ssh_key()),
                  "-o", "StrictHostKeyChecking=no",
                  "-o", "UserKnownHostsFile=" + os.devnull,
                  "-o", "LogLevel=quiet",
                  "-o", "ConnectTimeout=%d" % connect_timeout]
    if batch:
        cmd += ["-o", "BatchMode=yes"]
    if tty:
        cmd += ["-t"]
    return cmd + ["%s@%s" % (user, ip)]


class Remote(NamedTuple):
    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


def ssh_run(ip: str, script: str, timeout: float = 30, batch: bool = True) -> Remote:
    """Run a bash script on the robot. The script travels over stdin, so no
    quoting is needed. rc 255 means ssh itself failed (unreachable, no key)."""
    cmd = ssh_command(ip, batch=batch) + ["bash -s"]
    try:
        p = subprocess.run(cmd, input=script.encode("utf-8"), capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Remote(255, "", "timed out after %ds" % timeout)
    return Remote(p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"))


def ssh_read_file(ip: str, remote_path: str, timeout: float = 120) -> bytes:
    """Download one file from the robot (binary safe, no rsync/scp needed)."""
    cmd = ssh_command(ip) + ["cat " + shlex.quote(remote_path)]
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BhError("timed out downloading %s from %s" % (remote_path, ip))
    if p.returncode != 0:
        raise BhError("cannot read %s from %s" % (remote_path, ip))
    return p.stdout


def ssh_write_file(ip: str, remote_path: str, data: bytes, timeout: float = 60) -> None:
    """Upload one file to the robot atomically (write to .tmp, then rename)."""
    q = shlex.quote(remote_path)
    cmd = ssh_command(ip) + ["mkdir -p $(dirname %s) && cat > %s.tmp && mv %s.tmp %s" % (q, q, q, q)]
    try:
        p = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BhError("timed out uploading %s to %s" % (remote_path, ip))
    if p.returncode != 0:
        raise BhError("cannot write %s on %s: %s" % (remote_path, ip, p.stderr.decode("utf-8", "replace").strip()))


def ssh_interactive(ip: str, command: Optional[str] = None, batch: bool = True) -> int:
    """Open an interactive ssh session (or run a command with a terminal)."""
    cmd = ssh_command(ip, batch=batch, tty=command is not None)
    if command:
        cmd.append(command)
    return subprocess.call(cmd)


def explain_ssh_failure(target: Target, result: Remote) -> str:
    """Human-readable reason for an ssh failure."""
    if not tcp_open(target.ip):
        return "%s is not reachable (powered on? same network? try: ping %s)" % (target.label, target.ip)
    message = (result.err or result.out).strip()
    if result.rc == 255 and message.startswith("timed out after"):
        return ("%s: no answer, the command %s - it may still be running on the robot; "
                "check the card or 'bh status'" % (target.label, message))
    if result.rc == 255 and "Permission denied" in message:
        return ("%s refused the team ssh key - the robot is not set up for this code yet "
                "(run: bh robot install <name>)" % target.label)
    if result.rc == 255:
        return "%s: ssh failed: %s" % (target.label, message.splitlines()[-1] if message else "exit code 255")
    return "%s: %s" % (target.label, message or "exit code %d" % result.rc)


def local_ipv4() -> List[str]:
    """IPv4 addresses of this machine (best effort, no external traffic)."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    for probe in ("192.168.0.1", "10.0.0.1"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((probe, 9))  # UDP connect sends nothing; it only selects a route.
            found.append(s.getsockname()[0])
        except OSError:
            pass
        finally:
            s.close()
    unique = []
    for ip in found:
        if ip not in unique and not ip.startswith("127."):
            unique.append(ip)
    return unique
