"""bh doctor: is this computer ready to build, simulate and deploy?
bh setup: do the steps that can be automated."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

from . import core, deploy, policy
from .core import say

LINUX_TOOLS = ["clang", "cmake", "ninja", "rsync", "ccache", "mold"]
APT_LINE = "sudo apt install astyle ccache clang cmake llvm mold net-tools ninja-build rsync unzip"
DOCS_URL = "https://docs.b-human.de/coderelease2026/getting-started/"


class Report:
    def __init__(self):
        self.fails = 0
        self.warns = 0
        self.todo: List[str] = []

    def ok(self, msg: str) -> None:
        core.ok(msg)

    def warn(self, msg: str, fix: str = "") -> None:
        self.warns += 1
        core.warn(msg)
        if fix:
            self.todo.append(fix)

    def fail(self, msg: str, fix: str = "") -> None:
        self.fails += 1
        core.bad(msg)
        if fix:
            self.todo.append(fix)


def _run(cmd: List[str], timeout: int = 20) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=str(core.ROOT))
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def uninitialized_submodules() -> List[str]:
    rc, out = _run(["git", "submodule", "status"])
    if rc != 0:
        return []
    return [line.split()[1] for line in out.splitlines() if line.startswith("-") and len(line.split()) > 1]


def check_repository(r: Report) -> None:
    say("Repository  %s" % core.ROOT)
    path = str(core.ROOT)
    cloud = [c for c in ("OneDrive", "Dropbox", "iCloud", "Google Drive") if c.lower() in path.lower()]
    if cloud:
        r.warn("the checkout is inside a %s folder: builds write many gigabytes there and the sync client "
               "locks files during compilation" % cloud[0],
               "Move the repository out of the synced folder (for example to C:\\dev\\ or ~/dev/).")
    if " " in path:
        r.warn("the path contains spaces; some build scripts do not quote paths",
               "Move the repository to a path without spaces.")
    if not shutil.which("git"):
        r.fail("git not found", "Install git.")
    else:
        missing = uninitialized_submodules()
        if missing:
            r.fail("git submodules not initialized: %s (the simulator and build tools live there)" % ", ".join(missing),
                   "bh setup          (runs: git submodule update --init --recursive)")
        else:
            r.ok("git submodules are initialized")
    if (core.ROOT / "Util" / "onnxruntime" / "lib").is_dir():
        r.ok("ONNX Runtime libraries are present")
    else:
        r.warn("ONNX Runtime libraries not downloaded yet (bh generate fetches them)")


def check_tools(r: Report) -> None:
    say("Tools")
    r.ok("Python %d.%d.%d" % sys.version_info[:3])
    if shutil.which("ssh") or os.environ.get("BH_SSH"):
        r.ok("ssh client found")
    else:
        r.fail("ssh client not found (needed for everything that talks to a robot)",
               "Install the OpenSSH client (Windows: Settings > System > Optional features).")
    if core.is_windows():
        if shutil.which("cmake.exe"):
            r.ok("cmake found: " + _run(["cmake.exe", "--version"])[1].splitlines()[0])
        else:
            r.fail("cmake not found (needed to generate and build)", "Install CMake 4.2 or newer and add it to PATH.")
        vswhere = Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        version = _run([str(vswhere), "-latest", "-property", "installationVersion"])[1].strip() if vswhere.is_file() else ""
        if version.startswith("18."):
            r.ok("Visual Studio 2026 found (%s)" % version)
        else:
            r.fail("Visual Studio 2026 not found (needed to build the simulator on Windows)",
                   "Install Visual Studio 2026 with the 'Desktop development with C++' workload.")
        if core._wsl_available():
            rc, out = _run(["wsl.exe", "--exec", "bash", "-lc",
                            "for t in %s; do command -v $t >/dev/null 2>&1 || echo $t; done" % " ".join(LINUX_TOOLS)], 60)
            missing = out.replace("\x00", "").split()
            if rc == 0 and not missing:
                r.ok("WSL with all build tools found")
            else:
                r.fail("WSL is present but tools are missing: %s" % " ".join(missing),
                       "In WSL (Ubuntu 24.04): " + APT_LINE)
        else:
            r.fail("WSL is not set up (the robot software is cross-compiled and deployed from Linux tools)",
                   "Install WSL 1 with Ubuntu 24.04 (wsl --install -d Ubuntu-24.04, wsl --set-version Ubuntu-24.04 1), "
                   "then inside it: " + APT_LINE + "   Details: " + DOCS_URL)
    elif core.is_macos():
        for tool, why in (("xcodebuild", "Xcode"), ("rsync", "rsync")):
            if shutil.which(tool):
                r.ok("%s found" % why)
            else:
                r.fail("%s not found" % why, "Install %s." % why)
    else:
        missing = [t for t in LINUX_TOOLS if not shutil.which(t)]
        if missing:
            r.fail("build tools missing: %s" % " ".join(missing), APT_LINE)
        else:
            r.ok("all build tools found (%s)" % ", ".join(LINUX_TOOLS))


def check_build(r: Report) -> None:
    say("Build")
    generated = (deploy.robot_build_dir("Develop") is not None or core.is_macos()
                 and (core.ROOT / "Make" / "macOS" / "B-Human.xcodeproj").exists())
    if generated:
        r.ok("project files are generated")
    else:
        r.warn("project files are not generated yet", "bh generate      (once, and after adding/removing source files)")
    built = [c for c in core.BUILD_CONFIGS if deploy.binary_path(c).is_file()]
    if built:
        newest = deploy.newest_source_time()
        for c in built:
            if deploy.binary_path(c).stat().st_mtime < newest:
                r.warn("robot software (%s) is older than the source code" % c)
            else:
                r.ok("robot software (%s) is built and up to date" % c)
    else:
        r.warn("robot software not built yet", "bh build         (or just: bh deploy <robot>)")
    sims = [c for c in core.BUILD_CONFIGS if deploy.simrobot_path(c).exists()]
    if sims:
        r.ok("simulator built (%s)" % ", ".join(sims))
    else:
        r.warn("simulator not built yet", "bh sim           (builds it on first use)")


def check_config(r: Report) -> None:
    say("Configuration")
    s = core.settings()
    r.ok("team %s, default player %s, scenario %s, location %s"
         % (s.get("teamNumber"), s.get("playerNumber"), s.get("scenario"), s.get("location")))
    if not policy.check(quiet=True):
        r.fail("policy files do not match the configuration (see above)", "bh policy list")
    else:
        r.ok("policies (walk, kick, get-up) match their configured sizes")
    if core.find_config("ledHandler.cfg", "k1") is None:
        r.fail("Config/Scenarios/Default/ledHandler.cfg is missing - the robot software would not start")
    robots = core.load_robots()
    if robots:
        r.ok("%d robot(s) registered: %s" % (len(robots), ", ".join(x.name for x in robots)))
        unknown = [x.name for x in robots if x.model not in core.MODELS]
        if unknown:
            r.fail("robots without a type in Config/Robots/robots.cfg: %s" % ", ".join(unknown))
    else:
        r.warn("no robots registered yet", "bh robot onboard <name> --id <n>     (new robot)   or   bh robot add ...")


def cmd_doctor(args) -> int:
    r = Report()
    for check in (check_repository, check_tools, check_build, check_config):
        check(r)
        say()
    if r.todo:
        say("Next steps:")
        for i, step in enumerate(dict.fromkeys(r.todo), 1):
            say("  %d. %s" % (i, step))
    else:
        say("Everything is ready. Try:  bh sim      bh robot list      bh deploy <robot>")
    say()
    say("%d problem(s), %d warning(s)." % (r.fails, r.warns))
    return 1 if r.fails else 0


def cmd_setup(args) -> int:
    """Automate what can be automated: submodules and project generation."""
    missing = uninitialized_submodules()
    if missing:
        say("Git submodules to download: %s" % ", ".join(missing))
        if args.yes or core.confirm("Download them now (git submodule update --init --recursive)?"):
            rc = subprocess.call(["git", "submodule", "update", "--init", "--recursive"], cwd=str(core.ROOT))
            if rc != 0:
                raise core.BhError("git submodule update failed")
    r = Report()
    check_tools(r)
    if r.fails:
        say()
        say("Install the missing tools first:")
        for step in dict.fromkeys(r.todo):
            say("  - " + step)
        return 1
    if args.yes or core.confirm("Generate the project files now (downloads ONNX Runtime on first use)?"):
        deploy.generate()
    say("Setup finished. Check with: bh doctor")
    return 0


def cmd_test(args) -> int:
    """Run the tool's own tests. They use fake robots in a temporary directory:
    no robot, network or build is needed and the checkout is not changed."""
    import unittest
    base = Path(__file__).resolve().parents[1]
    suite = unittest.defaultTestLoader.discover(str(base / "tests"), pattern=args.pattern, top_level_dir=str(base))
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


def add_parsers(sub) -> None:
    p = sub.add_parser("doctor", help="check this computer and the repository, and say what to do next")
    p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("test", help="self-test of this tool against fake robots (safe, changes nothing, ~1 min)",
                       description=cmd_test.__doc__)
    p.add_argument("pattern", nargs="?", default="test_*.py", help="test files to run (default: test_*.py)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_test)
    p = sub.add_parser("setup", help="first-time setup: download submodules, generate project files")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask")
    p.set_defaults(func=cmd_setup)
