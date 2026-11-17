"""Neural-network policies (walk, kick, get-up): inspect, validate, switch, push.

Why this exists: the robot only checks that a policy matches the observation
layout with ASSERTs, and those are compiled out of Release builds. A policy
with the wrong input size would then run on garbage memory on a real robot.
``check()`` catches that on the laptop and runs before every deploy.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

from . import core, onnxinfo, share
from .core import BhError, say


class Slot(NamedTuple):
    key: str          # name used on the command line
    cfg: str          # configuration file holding the reference
    attr: str         # dotted attribute with the model file name
    n_in: str         # dotted attribute with the expected input size
    n_out: str        # dotted attribute with the expected output size
    subdir: str       # directory below Config/NeuralNets
    what: str


SLOTS = [
    Slot("walk", "walkingEngine.cfg", "walkNeuronalNetworkParameters.modelName",
         "walkNeuronalNetworkParameters.numInput", "walkNeuronalNetworkParameters.numOutput",
         "Walk", "walking (no ball nearby)"),
    Slot("kick", "walkingEngine.cfg", "ballNetworkParameters.kickModel",
         "ballNetworkParameters.numInput", "walkNeuronalNetworkParameters.numOutput",
         "Walk", "dribbling and kicking"),
    Slot("kick-soft", "walkingEngine.cfg", "oldBallNetworkParameters.kickModel",
         "oldBallNetworkParameters.numInput", "walkNeuronalNetworkParameters.numOutput",
         "Walk", "soft / precise kicks"),
    Slot("getup", "getUpEngine.cfg", "policyName", "numInput", "numOutput",
         "GetUpEngine", "getting up"),
    Slot("getup-fast", "getUpEngine.cfg", "policyNameFast", "numInputFast", "numOutputFast",
         "GetUpEngine", "fast get-up"),
]
SLOT_BY_KEY = {s.key: s for s in SLOTS}


class Finding(NamedTuple):
    cfg: Path
    slot: Slot
    model_file: str
    problem: Optional[str]   # None means fine
    detail: str


def nn_dir() -> Path:
    return core.config_dir() / "NeuralNets"


def model_of(cfg_path: Path) -> str:
    """Robot model ("k1"/"t1") a configuration file belongs to, from its path."""
    try:
        parts = cfg_path.relative_to(core.config_dir()).parts
    except ValueError:
        parts = cfg_path.parts
    for part in parts:
        if part in ("K1", "SimulatedK1"):
            return "k1"
        if part in ("T1", "SimulatedT1"):
            return "t1"
    return ""


def cfg_files(cfg_name: str, model: Optional[str] = None) -> List[Path]:
    """All copies of a configuration file (the defaults are duplicated per
    robot type, for the simulated robots and in some scenarios)."""
    files = sorted(core.config_dir().rglob(cfg_name))
    return [f for f in files if model is None or model_of(f) == model.lower()]


def inspect_model(path: Path) -> onnxinfo.ModelInfo:
    try:
        return onnxinfo.describe(path)
    except (onnxinfo.OnnxError, OSError) as e:
        raise BhError(str(e))


def signature_problem(info: onnxinfo.ModelInfo, n_in: Optional[int], n_out: Optional[int]) -> Optional[str]:
    """Why a model does not fit the given sizes (None if it does). Mirrors the
    ASSERTs in WalkingEngine::compile and GetUpEngine."""
    if len(info.inputs) != 1:
        return "has %d inputs, the engine feeds exactly one" % len(info.inputs)
    if len(info.outputs) != 1:
        return "has %d outputs, the engine reads exactly one" % len(info.outputs)
    got_in, got_out = info.inputs[0].size, info.outputs[0].size
    if got_in is None or got_out is None:
        return "has a dynamic (non-batch) dimension: %s -> %s" % (info.inputs[0], info.outputs[0])
    if len(info.inputs[0].dims) > 2 or len(info.outputs[0].dims) > 2:
        return "is not a flat vector model: %s -> %s" % (info.inputs[0], info.outputs[0])
    if n_in is not None and got_in != n_in:
        return "expects %d inputs but the configuration builds %d" % (got_in, n_in)
    if n_out is not None and got_out != n_out:
        return "produces %d outputs but the configuration expects %d" % (got_out, n_out)
    return None


def _int(value) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def findings(model: Optional[str] = None) -> List[Finding]:
    """Check every policy reference in every configuration copy."""
    result = []
    for slot in SLOTS:
        for cfg_path in cfg_files(slot.cfg, model):
            cfg = core.read_cfg(cfg_path)
            name = core.cfg_get(cfg, slot.attr)
            if name is None:
                continue
            n_in, n_out = _int(core.cfg_get(cfg, slot.n_in)), _int(core.cfg_get(cfg, slot.n_out))
            path = nn_dir() / slot.subdir / name
            if not path.is_file():
                result.append(Finding(cfg_path, slot, name, "file Config/NeuralNets/%s/%s does not exist"
                                      % (slot.subdir, name), ""))
                continue
            try:
                info = inspect_model(path)
            except BhError as e:
                result.append(Finding(cfg_path, slot, name, str(e), ""))
                continue
            problem = signature_problem(info, n_in, n_out)
            detail = "%s in, %s out" % (info.inputs[0].size if info.inputs else "?",
                                        info.outputs[0].size if info.outputs else "?")
            result.append(Finding(cfg_path, slot, name, problem, detail))
    return result


def check(model: Optional[str] = None, quiet: bool = False) -> bool:
    """Validate all policy references. Returns True if everything fits."""
    problems = [f for f in findings(model) if f.problem]
    for f in problems:
        core.bad("%s: %s policy %s %s" % (f.cfg.relative_to(core.ROOT).as_posix(), f.slot.key, f.model_file, f.problem))
    if not problems and not quiet:
        core.ok("all policy files exist and match the configured input/output sizes")
    return not problems


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_list(args) -> int:
    rows = []
    for f in findings(args.model):
        rows.append([f.slot.key, model_of(f.cfg).upper() or "-", f.model_file, f.detail,
                     "ok" if not f.problem else "PROBLEM: " + f.problem,
                     f.cfg.parent.relative_to(core.config_dir()).as_posix()])
    say("Policies in use (one line per configuration copy):")
    say(core.table(rows, ["slot", "robot", "file", "size", "status", "configured in Config/"]))
    say()
    say("Available policy files:")
    rows = []
    for sub in sorted({s.subdir for s in SLOTS}):
        for p in sorted((nn_dir() / sub).glob("*.onnx")):
            try:
                info = inspect_model(p)
                sig = "%s -> %s" % (", ".join(str(t) for t in info.inputs), ", ".join(str(t) for t in info.outputs))
            except BhError as e:
                sig = "unreadable: %s" % e
            rows.append(["%s/%s" % (sub, p.name), "%d kB" % (p.stat().st_size // 1024), sig])
    say(core.table(rows, ["file", "size", "signature"]))
    return 0 if all(not f.problem for f in findings(args.model)) else 1


def cmd_check(args) -> int:
    return 0 if check(args.model) else 1


def cmd_info(args) -> int:
    path = Path(args.file)
    if not path.is_file():
        for sub in sorted({s.subdir for s in SLOTS}):
            if (nn_dir() / sub / args.file).is_file():
                path = nn_dir() / sub / args.file
    if not path.is_file():
        raise BhError("no such file: %s" % args.file)
    info = inspect_model(path)
    say("file:     %s (%d kB)" % (path, path.stat().st_size // 1024))
    say("producer: %s, opset %s" % (info.producer or "?", info.opset if info.opset is not None else "?"))
    for t in info.inputs:
        say("input:    %s  -> %s values per step" % (t, t.size))
    for t in info.outputs:
        say("output:   %s  -> %s values per step" % (t, t.size))
    return 0


def _scope_filter(paths: List[Path], scope: str) -> List[Path]:
    if scope == "all":
        return paths
    selected = []
    for p in paths:
        rel = p.relative_to(core.config_dir()).parts
        simulated = rel[0] == "Robots" and rel[1].startswith("Simulated")
        if scope == "sim" and simulated:
            selected.append(p)
        elif scope == "robot" and not simulated:
            selected.append(p)
    return selected


def cmd_use(args) -> int:
    slot = SLOT_BY_KEY[args.slot]
    model = args.model.lower()
    source = Path(args.file)
    target_dir = nn_dir() / slot.subdir
    if not source.is_file():
        if (target_dir / args.file).is_file():
            source = target_dir / args.file
        else:
            raise BhError("policy file not found: %s" % args.file)
    info = inspect_model(source)
    paths = _scope_filter(cfg_files(slot.cfg, model), args.scope)
    paths = [p for p in paths if core.cfg_get(core.read_cfg(p), slot.attr) is not None]
    if not paths:
        raise BhError("no %s for %s found in scope %r" % (slot.cfg, model.upper(), args.scope))
    # Validate against every configuration that will be changed before touching anything.
    for p in paths:
        cfg = core.read_cfg(p)
        problem = signature_problem(info, _int(core.cfg_get(cfg, slot.n_in)), _int(core.cfg_get(cfg, slot.n_out)))
        if problem:
            raise BhError(
                "%s %s (%s).\nThe observation and action layout is built in C++ (%s); a policy with a "
                "different size needs matching code changes there and new numInput/numOutput values. "
                "See Docs/policies.md." % (source.name, problem, p.relative_to(core.ROOT).as_posix(),
                                           "Src/Modules/MotionControl/WalkingEngine" if slot.subdir == "Walk"
                                           else "Src/Modules/MotionControl/GetUpEngine"))
    dest = target_dir / source.name
    if source.resolve() != dest.resolve():
        if dest.exists() and dest.read_bytes() != source.read_bytes() and not args.overwrite:
            raise BhError("%s already exists with different content. Rename your file "
                          "(recommended, keeps old policies reproducible) or pass --overwrite." % dest)
        shutil.copyfile(str(source), str(dest))
        say("copied %s -> %s" % (source, dest.relative_to(core.ROOT).as_posix()))
    changed = 0
    for p in paths:
        text = core.read_exact(p)
        old = core.cfg_get(core.parse_cfg(text), slot.attr)
        if old == source.name:
            continue
        core.write_exact(p, core.cfg_replace(text, slot.attr, source.name))
        say("%s: %s %s -> %s" % (p.relative_to(core.ROOT).as_posix(), slot.key, old, source.name))
        changed += 1
    say("%d configuration file(s) changed for %s (%s)." % (changed, model.upper(), slot.what))
    if args.scope == "sim":
        say("Only the simulated robot uses it now. Try it:  bh sim   then, when happy:  bh policy use %s %s "
            "--model %s" % (slot.key, source.name, model))
    else:
        say("Next:  bh sim   (try it in simulation)   then   bh policy push <robot>   or   bh deploy <robot>")
    return 0


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def push_files(model: str) -> Dict[str, Path]:
    """Robot-side path -> local file for everything a policy change touches."""
    files: Dict[str, Path] = {}
    for slot in SLOTS:
        for cfg_path in cfg_files(slot.cfg, model):
            rel = cfg_path.relative_to(core.config_dir()).as_posix()
            if rel.startswith("Robots/Simulated"):
                continue
            files[core.REMOTE_CONFIG + "/" + rel] = cfg_path
            name = core.cfg_get(core.read_cfg(cfg_path), slot.attr)
            if name and (nn_dir() / slot.subdir / name).is_file():
                files["%s/NeuralNets/%s/%s" % (core.REMOTE_CONFIG, slot.subdir, name)] = nn_dir() / slot.subdir / name
    return files


def cmd_push(args) -> int:
    """Copy only the policy files and their configuration to robots - no build,
    no full deploy - then restart the robot software."""
    targets = core.resolve_targets(args.robots)
    rc = 0
    for t in targets:
        if t.robot is None or not t.robot.model:
            raise BhError("%s is not a registered robot (bh robot list)" % t.label)
        if not check(t.robot.model, quiet=True):
            raise BhError("fix the policy problems above first (nothing was sent)")
        files = push_files(t.robot.model)
        listing = core.ssh_run(t.ip, "cd / && sha256sum " + " ".join("'%s'" % p for p in files) + " 2>/dev/null; true")
        if listing.rc == 255:
            core.bad(core.explain_ssh_failure(t, listing))
            rc = 1
            continue
        remote = {}
        for line in listing.out.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                remote[parts[1].strip().lstrip("*")] = parts[0]
        todo = {r: l for r, l in files.items() if remote.get(r) != _sha256(l.read_bytes())}
        if not todo:
            core.ok("%s already has these policies" % t.label)
            continue
        say("%s: %d file(s) differ:" % (t.label, len(todo)))
        for r in todo:
            say("    " + r[len(core.REMOTE_CONFIG) + 1:])
        if args.dry_run:
            continue
        share.respect_claim(t, getattr(args, "ignore_claim", False))
        if not args.yes and not core.confirm(
                "Restarting makes the robot go limp for a moment. Is %s sitting or held?" % t.label):
            say("skipped %s" % t.label)
            continue
        stop = core.ssh_run(t.ip, "systemctl --user stop %s" % core.SERVICE)
        if not stop.ok:
            core.bad("could not stop the robot software on %s: %s" % (t.label, stop.err.strip()))
            rc = 1
            continue
        for r, l in todo.items():
            core.ssh_write_file(t.ip, r, l.read_bytes())
        start = core.ssh_run(t.ip, "systemctl --user start %s" % core.SERVICE)
        if start.ok:
            core.ok("%s: policies updated and robot software restarted" % t.label)
        else:
            core.bad("%s: files copied but the restart failed: %s" % (t.label, start.err.strip()))
            rc = 1
    return rc


def add_parser(sub) -> None:
    p = sub.add_parser("policy", help="inspect, validate, switch and push walk/kick/get-up policies",
                       description="Work with the neural-network policies (ONNX files in Config/NeuralNets).")
    ps = p.add_subparsers(dest="policy_cmd", metavar="<action>")
    ps.required = True

    q = ps.add_parser("list", help="show which policy every configuration uses and whether it fits")
    q.add_argument("--model", choices=["k1", "t1"], help="only this robot type")
    q.set_defaults(func=cmd_list)

    q = ps.add_parser("limits", help="the speeds, kick strengths and get-up times the policies are held to")
    q.add_argument("--model", choices=["k1", "t1"], default="k1")
    q.set_defaults(func=lambda args: __import__("bhtool.limits", fromlist=["cmd_limits"]).cmd_limits(args))

    q = ps.add_parser("check", help="verify that all policy files exist and match the configured sizes")
    q.add_argument("--model", choices=["k1", "t1"])
    q.set_defaults(func=cmd_check)

    q = ps.add_parser("info", help="print the input/output signature of an ONNX file")
    q.add_argument("file")
    q.set_defaults(func=cmd_info)

    q = ps.add_parser("use", help="switch a policy slot to a new ONNX file (validated first)")
    q.add_argument("slot", choices=sorted(SLOT_BY_KEY))
    q.add_argument("file", help="path to an .onnx file, or the name of one already in Config/NeuralNets")
    q.add_argument("--model", required=True, choices=["k1", "t1"], help="robot type the policy was trained for")
    q.add_argument("--scope", choices=["all", "sim", "robot"], default="all",
                   help="sim: only the simulated robot; robot: only real robots; all: both (default)")
    q.add_argument("--overwrite", action="store_true", help="replace an existing file of the same name")
    q.set_defaults(func=cmd_use)

    q = ps.add_parser("push", help="send only the policy files to robots and restart them (no build)")
    q.add_argument("robots", nargs="+", metavar="robot")
    q.add_argument("--dry-run", action="store_true", help="only show what would be sent")
    q.add_argument("-y", "--yes", action="store_true", help="do not ask before restarting")
    q.add_argument("--ignore-claim", action="store_true",
                   help="push although somebody else marked the robot as in use (bh robot claim)")
    q.set_defaults(func=cmd_push)
