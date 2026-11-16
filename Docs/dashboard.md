# Seeing what the robots are doing

Three views, from quick to complete:

| | Command | Shows | Cost for the robot |
|---|---|---|---|
| 1 | `bh status` | one line per robot | one short ssh command |
| 2 | `bh dash` | live page in the browser: robots, game, stop buttons | one short ssh command every 4 s |
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
request needs the token in that address.

**Per robot** (all registered robots, or the ones named on the command line):

- software state: running / not running / crashed / offline;
- battery with a bar, hottest joint, temperature of the computer;
- team and player number, scenario and location, LED mode it was deployed with;
- load, memory, free disk space, wireless signal strength;
- how long it has been switched on and when it was deployed;
- warnings: crash dump present, battery low, joint hot, disk nearly full, calibration not pulled, laptop
  guard armed;
- the last lines of its log, including everything it said aloud;
- **Stop** (robot software ends, robot goes limp), **Start**, **Restart**.

**The game** (top of the page, from GameController packets): state, half, remaining time, score, set play,
which GameController is sending, whether it was lost, and whether the own team is in that game. With a
GameController running each robot card also shows its penalty and the remaining penalty time.

**STOP ALL** ends the robot software on every robot (they go limp), stops the guard and sets the test
GameController's stop flag.

### Options

| Option | Adds |
|---|---|
| `--gc` | a practice GameController: buttons for initial / ready / set / playing / finished, **FREEZE** (all robots stand still) and release, goals, **Penalize** per robot. Each robot card then also shows what the robot itself reports once per second: where it thinks it is, when it last saw the ball, whether it has fallen. Not for official games - it refuses to start next to another GameController. |
| `--guard` | the laptop guard as buttons (**RUN** / **STOP**) for robots deployed with `--guard`. The robots stop when the tab is closed or the computer sleeps ([testing-and-safety.md](testing-and-safety.md)). |
| `--passive` | never contacts the robots: shows only what the GameController broadcasts. This is the only form to use **during an official game**. |
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
