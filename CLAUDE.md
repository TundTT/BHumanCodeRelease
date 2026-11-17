# Guide for coding agents (and new developers)

This repository is a RoboCup Humanoid Soccer team's robot software: a derivative of the **B-Human Code
Release 2026** for the Booster **K1** and **T1** robots. C++20, CMake, the SimRobot simulator, ONNX policies
for walking/kicking/getting up, and a Python command line (`bh`) for everything around it.

**Priority order for every change: 1. the robots win more games, 2. nobody and nothing gets hurt,
3. it is easy to use.** Never trade match performance for convenience.

User guides are in [Docs/](Docs/README.md). This file is what you need to change the code.

## Commands

Run from the repository root (`./bh` in bash, `bh` in cmd/PowerShell). Python 3.9+, no pip packages.

| Purpose | Command | Needs |
|---|---|---|
| what is missing on this machine | `bh doctor` | - |
| test the tooling (about 1 min) | `bh test` | Python, bash |
| one test file | `bh test test_units.py` | Python |
| check policies against their configuration | `bh policy check` | Python |
| build robot software / simulator | `bh build robot`, `bh build sim`, `bh build all -c Release` | toolchain + submodules |
| regenerate project files (after adding/removing sources) | `bh generate` | toolchain |
| simulated game / headless score table | `bh sim`, `bh eval --games 8 [--con X.con]` | simulator build |
| A/B test of a parameter file (one team gets it, sides swapped) | `bh eval --games 40 --variant Module=FILE.cfg` | simulator build |
| A/B test of any configuration files (tactics, strategy, `threads.cfg`) | `bh eval --games 40 --scenario NAME` (files in `Config/Scenarios/NAME/`) | simulator build |
| record game ground truth (falls, speeds, kicks); the numbers the policies are held to | `bh eval --stats PREFIX` then `bh stats PREFIX`; `bh policy limits` ([Docs/motion-requirements.md](Docs/motion-requirements.md)) | simulator build; Python |
| deploy to a robot, guarded | `bh deploy NAME --guard`, then `bh guard NAME` | robot |
| static checks of the C++ conventions | `bash Make/Common/checkParameters` (also `checkRequirements`, `checkConfigs`, `checkModules`, `checkDebugStatements`) | bash; several minutes each |

`bh <command> --help` is always accurate; the parser is the documentation.

## What you can verify, and what you must say when you cannot

| Change | Verify with | If that is not available |
|---|---|---|
| `Util/bh`, `Make/Common/deploy`, `setupTarget`, `Install` scripts | `bh test` (fake robots, real scripts) | - (always available) |
| configuration (`Config/**/*.cfg`) | `bh test test_units.py` parses every file; `bh policy check` | - |
| C++ | build (`bh build all`), then `bh sim`, then a robot under the guard | **say explicitly that it was not compiled** and run the static check scripts |
| behaviour / strategy | `bh eval --variant` (40 games or more, see below) | say it was not played |
| motion, policies | simulator, then robot held or on a soft surface with `--guard` | say it was not run on hardware |

A machine without the git submodules (`Util/SimRobot`, `Util/CompiledNN`, `Util/pybind11` are empty) or
without a C++ toolchain cannot compile anything - `bh doctor` tells you. Do not install toolchains or run
`bh setup` (downloads) without the user's consent. Never report an uncompiled C++ change as done.

## Map

```
bh, bh.cmd            launchers for Util/bh
Util/bh/bhtool/       the command line (one module per topic, see below)
Util/bh/tests/        its tests: fakerobot.py (stand-in robots), test_flows.py, test_units.py
Docs/                 user guides (one per task)
Src/Apps/             executables: Booster (robot), Tests, DeployDialog, CheckThreads
Src/Threads/          the four threads: Perception ("Color" in threads.cfg), Cognition, Motion, Audio
Src/Modules/          all algorithms, by area:
  Perception/           image processing, neural-network detectors
  Modeling/             self-localisation, ball, obstacles, team world model
  BehaviorControl/      StrategyBehaviorControl (team strategy, roles) -> SkillBehaviorControl
                        (CABSL options and skills) ; LEDHandler ; path planning ; ratings
  MotionControl/        WalkingEngine (RL policy), GetUpEngine (RL policy), FallEngine, head, arms
  Sensing/              falls, IMU, joint state        Infrastructure/  hardware, GameState, logging
  Communication/        GameController, team messages  Configuration/   calibration providers
Src/Representations/  the data types exchanged between modules (the blackboard)
Src/Libs/             framework (Framework/Module.h), streaming, math, platform, SimulatedRobot
Config/               everything tunable (see "Configuration")
Config/NeuralNets/    ONNX files: Walk/, GetUpEngine/, perception networks
Config/Scenes/        SimRobot scenes (*.ros3) and console scripts (*.con)
Make/Common/          shell scripts: deploy, downloadLogs, check*; Make/<OS>/ build entry points
Install/              what is put on a robot once (installRobot, Files/, Profiles/ for wireless)
Util/TestRunner/      headless game runner used by `bh eval`
Util/GameController/  protocol header (RoboCupGameControlData.h, version 20)
```

## How the robot software works (the 2-minute version)

- **Modules** compute **representations**. A module declares what it `REQUIRES`/`USES` and what it
  `PROVIDES` in a `MODULE(...)` macro; the framework orders the modules of a thread and passes
  representations between threads. There is no other coupling - search for `PROVIDES(X)` to find who
  computes `X`, for `REQUIRES(X)` to find who reads it.
- **Which module provides which representation in which thread** is configuration:
  `Config/Scenarios/<scenario>/<model>/threads.cfg`. A representation required in a thread must be provided
  there, received from another thread, or listed in `defaultRepresentations`.
- **Behaviour**: `StrategyBehaviorControl` chooses tactics and roles for the team
  (`Config/Behavior/{Strategies,Tactics,KickOffs,FreeKicks,PenaltyKicks}`), `SkillBehaviorControl` executes
  them as CABSL state machines (`Options/*.cpp` top level, `Skills/**` below). The top-level option list is
  `optionPlaySoccer.cfg`. Game and player state arrive in `GameState` (from `GameStateProvider`: buttons
  and GameController).
- **Motion**: `MotionEngine` selects one motion phase at a time. `WalkingEngine` and `GetUpEngine` run ONNX
  policies; they build the observation vector in C++ and the sizes are only checked by `ASSERT`.
- **Hardware**: `BoosterProvider` (Motion thread) talks to the robot's firmware (joints, IMU, LED, buttons).

The upstream documentation covers the framework in depth: <https://docs.b-human.de/coderelease2026/>.

## Conventions that bite

**Module and parameters**

```cpp
MODULE(MyModule,
{,
  REQUIRES(FrameInfo),
  PROVIDES(MyRepresentation),
  LOADS_PARAMETERS(
  {,
    (int) someTime, /**< What it is (in ms). */
  }),
});
```

- `LOADS_PARAMETERS` reads `myModule.cfg` (module name with the leading capitals lower-cased) and **asserts
  that the file exists**. A parameter that is missing in the file is only reported as an error message
  ("attribute ... not found") and then has no defined value; an entry the module does not know is reported
  as "unused attribute". So a new parameter needs a value in every copy of that file
  (`find Config -name myModule.cfg`). `DEFINES_PARAMETERS` gives the defaults in code instead.
- The block starts with `{,` - or with `{` directly followed by an `ENUM(...)` definition when the first
  entry is an enum (see `LEDHandler.h`).
- A new module does nothing until a `threads.cfg` names it as provider - in **every** scenario and model
  that should use it (`Config/Scenarios/*/{K1,T1}/threads.cfg`).
- Adding or removing a source file: `bh generate` (sources are globbed by CMake).
- `Make/Common/checkParameters` and `checkRequirements` find unused parameters and requirements.
  `checkParameters` reports one finding in `SkillBehaviorControl` that predates this fork.

**Configuration search path** - first match wins (mirrors `Settings::updateSearchPath`, tested in
`test_units.py`):

```
Config/Robots/<robot name>/
Config/Locations/<location>/<model>/ , Config/Locations/<location>/          (if not Default)
Config/Scenarios/<scenario>/<model>/ , Config/Scenarios/<scenario>/          (if not Default)
Config/Robots/Default/<model>/ , Config/Robots/Default/
Config/Locations/Default/<model>/ , Config/Locations/Default/
Config/Scenarios/Default/<model>/ , Config/Scenarios/Default/ , Config/
```

Simulated robots are named `SimulatedK1` / `SimulatedT1`. So the same file name can exist in several places;
change the copy that is actually used and check the others (`bh policy list` shows this for the policies).

**Build configurations**: Debug and Develop keep `ASSERT`/`VERIFY` (Develop is optimised, `-UNDEBUG`);
**Release compiles assertions out**. Anything that is only guarded by an assertion is unguarded in a match.
That is why `bh` checks policy sizes before every deploy.

**Style**: 2 spaces, LF line endings, braces on their own line, `theXyz` for required representations,
doxygen comments on parameters. Follow the file you are in. `Make/Common/bstyle` formats C++.

## Performance-critical code - be careful here

| Where | Why |
|---|---|
| `Src/Threads/Motion*`, `Modules/MotionControl/**`, `BoosterProvider` | hard real-time loop; a late cycle is a stumbling robot. No allocation, logging, file or network access in `update()` paths. |
| `BoosterProvider` LED code | every LED colour change is a synchronous RPC from the motion thread (see [Docs/leds.md](Docs/leds.md)). Never make the LED animate while the robot plays. |
| `Modules/Perception/**` | runs per camera frame; the neural networks dominate the frame time. Measure before and after (`get representation:RobotHealth` for frame rates). |
| `WalkingEngine` / `GetUpEngine` observation code | must match the training environment value by value; a silent mismatch degrades the walk without any error. |
| team communication | the message budget per game is limited by the rules; every extra message costs. |

Rules of thumb: measure in the simulator with many games, not one; change one thing at a time; keep
parameters in `.cfg` files so that variants can be compared with `bh eval --variant` or `--scenario` (new behaviour gets a
parameter that switches it, so one binary plays both). The goal difference of a single game between
identical teams scatters by about 3.7 goals: 20 games only show effects above 1.6 goals per game, 200
games above half a goal. Do not adopt a change on fewer games than its effect needs
([Docs/simulation.md](Docs/simulation.md#measuring-instead-of-watching)); when in doubt about
motion code, leave it and write down what you found.

## The `bh` tool

`Util/bh/bhtool/`, standard library only, Python 3.9 compatible:

| Module | Commands |
|---|---|
| `core.py` | shared: cfg parser (`parse_cfg`, `cfg_replace` keeps formatting), robots, ssh, running repo scripts, search path |
| `doctor.py` | `doctor`, `setup`, `test` |
| `deploy.py` | `deploy`, `match`, `build`, `generate`; calibration rescue, guard arming, verification |
| `robots.py` | `robot list/add/remove/scan/install/quarantine/onboard` |
| `share.py` | `robot claim/release/backup/backups/restore`; the claim check of `deploy`, `policy push`, `restore` |
| `remote.py` | `status`, `stop`, `start`, `restart`, `logs`, `ssh`, `reboot`, `shutdown` |
| `guard.py` | `guard` (heartbeat to `EmergencyButtonProvider`, UDP 9910) |
| `policy.py`, `onnxinfo.py` | `policy list/check/info/use/push`; ONNX shapes without onnx/protobuf packages |
| `calib.py` | `calib status/pull` |
| `gamecontroller.py` | `gc doctor/listen/test`; protocol encode/decode |
| `sim.py` | `sim`, `connect`, `eval` |
| `dash.py`, `dash.html` | `dash`: page, background jobs (`core.capture_output` sends their output to the page), uploads, per-robot guard heartbeats |

Rules:

- The tool wraps the repository's own scripts (`Make/Common/deploy`, `Install/installRobot`,
  `Make/Common/downloadLogs`) instead of re-implementing them.
- Every command that makes a robot go limp says so. Those that are neither a deploy nor a stop also ask
  first (`-y` skips the question).
- Anything that stops a robot must keep working when everything else is broken: `bh stop` has no
  dependencies beyond ssh.
- New behaviour gets a test. `fakerobot.py` provides `ssh`/`rsync`/`systemctl` stand-ins that operate on a
  directory tree per robot, so flows run for real without hardware. Tests that compare tool constants with
  the C++ sources or configuration (protocol constants, thresholds, attribute names) are deliberate: they
  fail when the two drift apart - fix the drift, do not delete the test.
- User-visible facts live in three places that must agree: the code, `Docs/`, and help texts. The LED
  meanings additionally in `dash.py` (`LED_LEGEND`).

## Pitfalls collected so far

- Text files are LF (`.gitattributes`). `bh` (the bash launcher) must keep its executable bit:
  `git update-index --chmod=+x bh`.
- `.gitignore` ignores every dot-file and dot-directory, and deploying excludes them too.
- Deploying replaces the robot's whole `Config` directory (`rsync --del`). Whatever exists only on the robot
  is lost unless it is rescued first - `bh deploy` rescues calibration and robot-built TensorRT engines;
  `Make/Common/deploy` alone does not.
- A robot whose software stops goes limp and falls. Applies to deploy, restart, log download, policy push.
- On Windows the robot build and all shell scripts run in WSL or Git Bash; `Util/bh/bhtool/core.py`
  (`run_script`) hides that. A checkout inside OneDrive/Dropbox breaks builds.
- Copying the checkout from Windows to Linux with tar/scp turns the four symlinks (e.g.
  `Make/Linux/CMakeLists.txt`) into text files and drops the executable bits; CMake then fails with a parse
  error. Clone with git on Linux, or `git config core.symlinks true` and check the links out again.
- Headless Linux: the simulator needs a display (`xvfb`, started by `bh eval` itself) and a sound device
  (`SoundPlayer` asserts without one; `ALSA_CONFIG_PATH` to a file with `pcm.default { type null }` helps).
- Git Bash rewrites paths in arguments; when a bash script starts a native program, see how
  `Util/bh/tests/fakerobot.py` handles it before debugging "file not found".
- Robot firmware 1.7.1 is the tested one; 1.8.0 misbehaves.

## Open findings (not changed, because they could not be tested)

- `WalkingEngine::getMaxRotationToStepFactor` and `getStepSizeFactor`
  ([WalkingEngine.cpp:523](Src/Modules/MotionControl/WalkingEngine/WalkingEngine.cpp:523) and 535): for a
  fast walk the *normal* offset (`reduceTranslationFromRotationBaseOffset`) is combined with the *fast*
  threshold and vice versa, contradicting the parameter comments in `WalkingEngine.h`. Whether the shipped
  parameters were tuned around this is unknown. Test before changing: swapping the two offset values for
  one team with `bh eval --variant WalkingEngine=...` shows the effect of the "corrected" pairing without
  touching code.
- `Config/Scenes/Includes/AutomaticCameraCalibration.con` refers to a behaviour option that does not exist
  in this release; the calibration behaviour always runs the manual procedure.

## Licence

[License.md](License.md) applies to everything derived from this code: keep the notices, announce the use
before a competition, publish the derived software afterwards (summary in
[Docs/README.md](Docs/README.md#licence-obligations)). Do not remove author or copyright lines.
