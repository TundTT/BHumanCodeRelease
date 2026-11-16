"""The numbers the motion policies are held to: ``bh policy limits``.

Everything here is read from the configuration files the robot reads (walk limits, kick table, get-up time
budgets), so it cannot drift from the code the way a document can. What the numbers mean is explained in
Docs/motion-requirements.md.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import core, policy
from .core import BhError, say


def number(value) -> Optional[float]:
    """A configuration value such as ``120deg`` or ``2000`` as a float (degrees stay degrees)."""
    match = re.match(r"-?\d+(\.\d+)?", str(value))
    return float(match.group()) if match else None


def where(path: Path) -> str:
    """Where a configuration file lives, as the search path names it (Robots/Default/K1, Scenarios/...)."""
    relative = path.parent.relative_to(core.config_dir()).as_posix()
    return relative or "Config"


def table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    widths = [max(len(str(r[i])) for r in [header] + list(rows)) for i in range(len(header))]
    for r in [header] + list(rows):
        say("  " + "  ".join(str(c).ljust(w) if i == 0 else str(c).rjust(w) for i, (c, w) in enumerate(zip(r, widths))))


def _g(data: dict, key: str, digits: int = 0) -> str:
    value = number(core.cfg_get(data, key))
    return "-" if value is None else ("%.*f" % (digits, value))


def friction(model: str) -> Optional[float]:
    """Deceleration of the rolling ball in m/s^2 (positive)."""
    path = core.find_config("ballSpecification.cfg", model)
    value = number(core.cfg_get(core.read_cfg(path), "staticFriction")) if path else None
    return abs(value) if value else None


def rolling_distance(velocity: float, deceleration: Optional[float]) -> Optional[float]:
    """How far a ball that leaves with ``velocity`` (mm/s) rolls until it stops (mm)."""
    if not deceleration:
        return None
    return velocity * velocity / (2.0 * deceleration * 1000.0)


def walk_rows(model: str) -> Tuple[List[str], List[List[str]]]:
    files = policy.cfg_files("walkingEngine.cfg", model)
    data = [core.read_cfg(f) for f in files]
    header = ["walk (mm/s, deg/s)"] + [where(f) for f in files]
    rows: List[List[str]] = []
    for label, base in (("fast", "fastWalkSpeed"), ("normal", "normalWalkSpeed"), ("slow", "slowWalkSpeed")):
        for what, key, digits in (("forward", "maxSpeed.translation.x", 0), ("sideways", "maxSpeed.translation.y", 0),
                                  ("backwards", "maxSpeedBackwards", 0), ("turning", "maxSpeed.rotation", 0)):
            rows.append(["%s %s" % (label, what)] + [_g(d, "configuredParameters.%s.%s" % (base, key), digits) for d in data])
    for what, key in (("acceleration x", "maxAcceleration.x"), ("acceleration y", "maxAcceleration.y"),
                      ("deceleration x", "maxDeceleration.x")):
        rows.append([what + " (mm/s per step)"] + [_g(d, "commonSpeedParameters." + key) for d in data])
    rows.append(["gait frequency, Hz (walk)"] + [
        "%s +-%s" % (_g(d, "frequencyParametersWalk.base", 1), _g(d, "frequencyParametersWalk.clipRange.max", 1)) for d in data])
    rows.append(["walk policy (in/out)"] + [
        "%s (%s/%s)" % (core.cfg_get(d, "walkNeuronalNetworkParameters.modelName", "-"),
                        _g(d, "walkNeuronalNetworkParameters.numInput"), _g(d, "walkNeuronalNetworkParameters.numOutput"))
        for d in data])
    return header, rows


def kick_table(model: str) -> Tuple[List[str], List[List[str]]]:
    path = core.find_config("kickInfo.cfg", model)
    if path is None:
        raise BhError("kickInfo.cfg not found")
    kicks = core.cfg_get(core.read_cfg(path), "kicks", {})
    decel = friction(model)
    header = ["kick", "ball speed mm/s", "distance it rolls, mm", "time ms"]
    rows = []
    for name, kick in kicks.items():
        low, high = number(core.cfg_get(kick, "ballVelocity.min")), number(core.cfg_get(kick, "ballVelocity.max"))
        if low is None or high is None:
            continue
        d_low, d_high = rolling_distance(low, decel), rolling_distance(high, decel)
        rows.append([name, "%.0f - %.0f" % (low, high) if low != high else "%.0f" % low,
                     "-" if d_low is None else ("%.0f - %.0f" % (d_low, d_high) if low != high else "%.0f" % d_low),
                     _g(kick, "executionTime")])
    return header, rows


def getup_rows(model: str) -> Tuple[List[str], List[List[str]]]:
    files = policy.cfg_files("getUpEngine.cfg", model)
    data = [core.read_cfg(f) for f in files]
    header = ["get up"] + [where(f) for f in files]
    rows: List[List[str]] = []

    def last_time(d: dict, key: str) -> Optional[float]:
        entries = core.cfg_get(d, key, [])
        return number(entries[-1].get("executionTime")) if entries else None

    def seconds(d: dict, key: str) -> str:
        t, factor = last_time(d, key), number(core.cfg_get(d, "speedFactor"))
        return "-" if not t or not factor else "%.1f" % (t / factor / 1000.0)

    rows.append(["fast policy used (real robot)"] + [str(core.cfg_get(d, "fastActive", "-")) for d in data])
    rows.append(["speed factor"] + [_g(d, "speedFactor", 1) for d in data])
    rows.append(["tries before it asks for help"] + [_g(d, "maxTryCounter") for d in data])
    rows.append(["must stand taller than, mm"] + [_g(d, "minStandHeightWhenDone") for d in data])
    rows.append(["slow policy: lying on the front, s"] + [seconds(d, "frontInfo") for d in data])
    rows.append(["slow policy: lying on the back, s"] + [seconds(d, "backInfo") for d in data])
    return header, rows


def cmd_limits(args) -> int:
    model = args.model
    deceleration = friction(model)
    say("== Walk: the fastest the walk policy is ever asked to go (%s)" % model.upper())
    header, rows = walk_rows(model)
    table(header, rows)
    say()
    say("== Kicks: what the behaviour may ask the kick policy for (%s)" % model.upper())
    header, rows = kick_table(model)
    table(header, rows)
    if deceleration:
        say("  The ball slows down by %.2f m/s^2, so it rolls v^2 / (2 * %.2f) after leaving the foot." % (deceleration, deceleration))
    for file in policy.cfg_files("walkingEngine.cfg", model):
        data = core.read_cfg(file)
        for name in ("ballNetworkParameters", "oldBallNetworkParameters"):
            low, high = number(core.cfg_get(data, name + ".kickVelocityRange.min")), number(core.cfg_get(data, name + ".kickVelocityRange.max"))
            if low is not None and high is not None:
                say("  %-28s %-24s kick policy is asked for %.0f - %.0f mm/s" % (where(file), name, low, high))
    say()
    say("== Getting up")
    header, rows = getup_rows(model)
    table(header, rows)
    say()
    say("Meaning of all of this: Docs/motion-requirements.md. Measured speeds, falls and kicks of simulated games: bh stats.")
    return 0
