"""Command line entry point: ``bh <command> ...``."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from . import __version__, calib, core, deploy, doctor, gamecontroller, guard, policy, remote, robots, sim

OVERVIEW = """\
first time        bh doctor                 what is missing on this computer
                  bh setup                  download submodules, generate project files
simulation        bh sim [scene]            start a simulated game (bh sim --list)
                  bh eval --games 8         play headless games, print the scores
                  bh eval --variant ...     A/B test (also --scenario); --stats P records, bh stats P evaluates
new robot         bh robot onboard NAME --id N      register + inspect + install
                  bh robot scan NAME        is foreign software interfering?
every day         bh deploy NAME            build, copy, verify, start
                  bh status                 battery, temperature, state of all robots
                  bh logs NAME -f           follow what the robot says
                  bh dash                   live dashboard in the browser
                  bh connect NAME           full mission control (SimRobot attached)
safety            bh deploy NAME --guard    robot moves only while 'bh guard' runs
                  bh guard                  dead-man switch (ENTER = run/stop)
                  bh stop                   HARD STOP all robots (they go limp)
policies          bh policy list            which networks are used, do they fit
                  bh policy use walk FILE --model k1 --scope sim
                  bh policy push NAME       send only the policies, restart
calibration       bh calib status           robot vs repository
game controller   bh gc doctor              why do robots (not) hear the referee
                  bh gc test                practice GameController
match day         bh match alice:1 bob:2 --profile HSL_A

Guides: Docs/README.md      For coding agents: CLAUDE.md
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bh", description="One command line for building, testing, deploying and watching the robots.",
        epilog=OVERVIEW, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version="bh " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    doctor.add_parsers(sub)
    deploy.add_parsers(sub)
    sim.add_parsers(sub)
    robots.add_parser(sub)
    remote.add_parsers(sub)
    guard.add_parser(sub)
    policy.add_parser(sub)
    calib.add_parser(sub)
    gamecontroller.add_parser(sub)
    from . import dash, stats
    dash.add_parser(sub)
    stats.add_parser(sub)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except core.BhError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
