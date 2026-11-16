# Working with this code

Everything you do day to day goes through one command, `bh`, run from the repository root
(`./bh` in a Linux/macOS/Git Bash shell, `bh` in Windows cmd/PowerShell). `bh --help` shows the overview,
`bh <command> --help` the details. It only needs Python 3.9+; nothing has to be installed with pip
(except `psutil` for `bh eval`).

| I want to ... | Guide | First command |
|---|---|---|
| set up this computer | [quickstart.md](quickstart.md) | `bh doctor` |
| put this code on a new or second-hand robot | [robot-onboarding.md](robot-onboarding.md) | `bh robot onboard NAME --id N` |
| build, deploy, test safely | [testing-and-safety.md](testing-and-safety.md) | `bh deploy NAME --guard` |
| try a new walk / kick / get-up policy | [policies.md](policies.md) | `bh policy use walk FILE --model k1 --scope sim` |
| know what speeds, kicks and get-ups the policies must reach | [motion-requirements.md](motion-requirements.md) | `bh policy limits` |
| run a game in the simulator | [simulation.md](simulation.md) | `bh sim` |
| calibrate a robot, close the sim-to-real gap | [calibration.md](calibration.md) | `bh calib status` |
| connect to the GameController | [game-controller.md](game-controller.md) | `bh gc doctor` |
| see what the robots are doing | [dashboard.md](dashboard.md) | `bh dash` |
| read the LED on the robot's back | [leds.md](leds.md) | |
| play a real match | [match-day.md](match-day.md) | `bh match alice:1 bob:2 --profile HSL_A` |
| change the code (people and coding agents) | [../CLAUDE.md](../CLAUDE.md) | `bh test` |

The upstream documentation of the B-Human system itself (architecture, modules, SimRobot console) is at
<https://docs.b-human.de/coderelease2026/>. These guides only cover what is specific to this repository.

## The three ways to stop a robot

Know these before you switch a robot on.

| | What | Effect | When |
|---|---|---|---|
| 1 | **F1 button** on the robot | robot goes limp at once | always works, no network needed |
| 2 | `bh stop` (no name = all robots) or **STOP ALL** in the dashboard | ends the robot software, robot goes limp | needs network |
| 3 | `bh guard` / dashboard guard / GameController "stop" | robot stands still but stays up | soft stop, see [testing-and-safety.md](testing-and-safety.md) |

A limp robot falls if nobody holds it. Hold the robot or have it sit before 1 and 2 whenever you can.

## Licence obligations

This is a derivative of the B-Human Code Release 2026. [License.md](../License.md) applies; in short:

- keep the copyright notice and licence in redistributed source and binaries;
- announce the use of this code on the Humanoid Soccer League mailing list at least one month before the
  first competition in which it is used, naming the parts used;
- if major parts of the system are used (that is the case here), describe your own contribution in that
  announcement and publish your derived software by the end of the year of the competition, as detailed
  in the licence.

Read the licence itself before a competition; this summary does not replace it.
