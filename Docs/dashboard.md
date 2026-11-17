# Seeing what the robots are doing

Three views, from quick to complete:

| | Command | Shows | Cost for the robot |
|---|---|---|---|
| 1 | `bh status` | one line per robot | one short ssh command |
| 2 | `bh dash` | live page in the browser: robots with their LED, game, and buttons for deploy, policies, guard, sharing | one short ssh command every 4 s |
| 3 | `bh connect NAME` | everything: camera, world model, behaviour, every joint | a debug connection - **not during a game** |

And without a computer: the LED on the robot's back ([leds.md](leds.md)) and what the robot says aloud
(also in `bh logs NAME -f`).

## The dashboard

```bash
./bh dash
```

```bash
./bh dash --gc --guard
```

It opens `http://127.0.0.1:8089/?t=...` in the browser. The page is served to this computer only and every
request needs the token in that address. Every button runs the same code as the matching `bh` command; the
command and its output appear in the **Activity** bar at the bottom of the page.

### Robots tab: one card per robot

(All registered robots, or the ones named on the command line.)

- **The back LED**, drawn on a robot figure in the colour the robot software reports, blinking like the
  real one, with its meaning underneath. The robot software writes its LED state next to its status file
  (`InternalStateProvider`, at most once a second, and only states that lasted half a second, so the short
  test-mode flashes do not show). A robot whose software is older than this shows "no LED report" until it
  is deployed again; a robot whose software is stopped shows a grey LED.
- software state (running / not running / crashed / offline), robot type, and who is using the robot
  (**free**, **yours** or **in use by NAME** with their note - see [sharing-robots.md](sharing-robots.md));
- gauges: battery, hottest joint, robot computer, wireless signal, free disk;
- warnings (crash dump, battery low, joint hot, disk nearly full, calibration not pulled, laptop guard
  armed). Click one for what it means and what to do;
- buttons:

| Button | Same as | Note |
|---|---|---|
| **Deploy…** | `bh deploy` | build, scenario (with the walk speed it allows), player number, LED mode, guard, back up first, dry run. Asks you to confirm that the robot sits or is held; if somebody else claimed the robot you must confirm that you asked them |
| **Send policies…** | `bh policy push` | **Compare** lists what differs; **Send and restart** copies and restarts |
| **Drive with gamepad** | - | the gamepad buttons, with the robot's LED live (it blinks slowly while the gamepad is in control) |
| **Stop** / **Start** / **Restart** | `bh stop`, `bh start`, `bh restart` | Stop never asks |
| **Claim** / **Release** | `bh robot claim`, `bh robot release` | |
| **Back up** / **Restore…** | `bh robot backup`, `bh robot restore` | Restore lists the backups on the robot |
| **Scan**, **Log**, **Crash dump** | `bh robot scan`, `bh logs` | read only |
| **Penalize** (with `--gc`) | test GameController | |

- **Laptop guard box** - shown for a robot deployed with the guard. **RUN** starts a heartbeat for this
  robot only and lets it move; **STOP** makes it stand still; **End heartbeat** stops sending (the robot
  stays stopped). The heartbeat goes only to robots you pressed RUN for, so the page never overrides the
  guard of a robot somebody else is testing. Closing the page or a sleeping computer means STOP.
- **Details** - team and player number, scenario, location, load, memory, uptime, when it was deployed, and
  the last lines of its log, including everything it said aloud.

### Policies tab

1. Drop `.onnx` files onto the page (they are kept in `Build/Dash/uploads`). Each file shows its input and
   output size and, per robot type, which slots it fits.
2. Drag a file onto a slot (walk, kick, kick-soft, getup, getup-fast) and choose **the simulator only**,
   **the real robots** or **both** - this is `bh policy use`, so a file that does not fit is refused before
   anything changes. The slot shows which file the real robots and the simulator use.
3. **Try in the simulator** starts `bh sim`.
4. **Send to NAME…** is `bh policy push` (or deploy).

Using a file changes `Config/` in your checkout, exactly like the command; undo with git.

### Help tab

The LED colours with blinking examples, and short answers to "how do I...".

**The game** (top of the page, from GameController packets): state, half, remaining time, score, set play,
which GameController is sending, whether it was lost, and whether the own team is in that game. With a
GameController running each robot card also shows its penalty and the remaining penalty time.

**STOP ALL** ends the robot software on every robot (they go limp), sets every guard heartbeat to STOP and
sets the test GameController's stop flag.

Only one job runs per robot at a time, and jobs that read or change the configuration (deploy, sending
policies, using a policy) run one after the other.

### Options

| Option | Adds |
|---|---|
| `--gc` | a practice GameController: buttons for initial / ready / set / playing / finished, **FREEZE** (all robots stand still) and release, goals, **Penalize** per robot. Each robot card then also shows what the robot itself reports once per second: where it thinks it is, when it last saw the ball, whether it has fallen. Not for official games - it refuses to start next to another GameController. |
| `--guard` | a guard heartbeat to **all** robots from the start, with **RUN** / **STOP** at the top. Without it the page starts a heartbeat per robot when you press RUN on its card. The robots stop when the tab is closed or the computer sleeps ([testing-and-safety.md](testing-and-safety.md)). |
| `--as NAME` | your name on robots you claim (default: `BH_USER`, then your git user name) |
| `--passive` | never contacts the robots: shows only what the GameController broadcasts; all robot buttons are refused. This is the only form to use **during an official game**. |
| `--interval S` | seconds between robot polls (default 4) |
| `--players N` | team size for `--gc` (default 5) |
| `--port N`, `--no-browser` | where to serve; do not open the browser |

What the dashboard does not show: individual joint angles and temperatures, camera images, the behaviour's
decisions. Those need a debug connection - the next section.

## Mission control: SimRobot attached to a robot

```bash
./bh connect Alice
```

starts SimRobot's `Remote` scene already connected to the robot (cable or wireless, whichever answers). It
shows live what the robot computes:

| View (scene graph on the left) | Content |
|---|---|
| image views | camera image with the detected lines, ball, robots, penalty mark, centre circle drawn in |
| `worldState` field view | where the robot thinks it, the ball, team-mates and opponents are |
| behaviour view | the active options and states of the behaviour, top down - *what it is doing and why* |
| joint view | requested and measured angle of every joint, motor parameters |
| console | every command of `help`; text messages of the robot |

Useful console commands:

| Command | Effect |
|---|---|
| `get representation:RobotHealth` | battery, temperatures, frame rates and execution times as the robot reports them |
| `get representation:GameState` | game and player state |
| `get representation:JointSensorData` | angle, torque, temperature and temperature status of every joint |
| `get module:<Module>` / `set module:<Module> ...` | read / change a module's parameters while it runs |
| `dr module:GameStateProvider:unstiff` | make the robot limp |
| `dr module:GameStateProvider:active` | make it stand up (like holding STAND) |
| `dr module:GameStateProvider:calibration` | calibration mode ([calibration.md](calibration.md)) |
| `vid all <drawing> [on|off]`, `vfd worldState <drawing>` | switch drawings in image / field views (`?` lists them) |
| `log start` / `log stop` / `log save <file>` | record what is being sent |
| `help <pattern>` | everything else |

Changes made with `set` are gone after the next restart of the robot software; put the ones worth keeping
into the configuration file of that module.

A debug connection costs the robot computing time and network bandwidth (camera images!). Use it for
testing, close it before a game - `bh match` reminds you.

## From scripts

```bash
./bh status --json
```

prints everything the dashboard knows about each robot as JSON; the exit code is 1 if a robot is not
reachable.
