# Quick start

## 1. Check the computer

```bash
./bh doctor
```

`bh doctor` inspects the repository, the installed tools, the build and the configuration, and ends with a
numbered list of what to do next. Repeat it until it reports no problems. It changes nothing.

What a complete setup needs (the doctor tells you which of these are missing):

| | Linux | Windows | macOS |
|---|---|---|---|
| Python 3.9+ | yes | yes | yes |
| Git submodules | `bh setup` | `bh setup` | `bh setup` |
| Simulator build | clang, cmake, ninja | Visual Studio 2026 (C++ workload), CMake | Xcode |
| Robot build and deploy | same tools + rsync | WSL 1 with Ubuntu 24.04 and the Linux tools | Xcode + rsync |

Keep the checkout **outside OneDrive/Dropbox** folders: a build writes several gigabytes and sync clients
lock files while the compiler needs them.

## 2. First-time setup

```bash
./bh setup
```

Downloads the git submodules (simulator, build helpers) and generates the project files. It asks before each
step; `-y` answers yes.

## 3. See it play in the simulator

```bash
./bh sim
```

Builds the simulator on first use and opens a 5 vs 5 game. Type `gc playing` in the simulator console to
kick off. More in [simulation.md](simulation.md).

## 4. Put it on a robot

```bash
./bh robot onboard Alice --id 14
```

```bash
./bh deploy Alice --guard
```

```bash
./bh guard Alice
```

The first command registers the robot, shows what is on it, and installs this code's environment
([robot-onboarding.md](robot-onboarding.md)). The second builds and copies the software and arms the laptop
guard, so the robot only moves while the third command is running and set to RUN
([testing-and-safety.md](testing-and-safety.md)).

## 5. Check the tool itself

```bash
./bh test
```

Runs the tool's own tests against fake robots in a temporary directory (about a minute). No robot, network
or build is needed and nothing in the checkout is changed. Run it after changing anything in `Util/bh`,
`Make/Common` or `Install`.

## Command overview

```
first time        bh doctor                 what is missing on this computer
                  bh setup                  download submodules, generate project files
simulation        bh sim [scene]            start a simulated game (bh sim --list)
                  bh eval --games 8         play headless games, print the scores
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
```

Robots are always addressed by name (`Alice`) or address. A name uses the cabled address if it answers,
otherwise the wireless one.
