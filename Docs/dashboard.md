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

To learn the page first without a real robot: `./bh dash --practice`
([below](#practising-on-simulated-robots)).

It opens `http://127.0.0.1:8089/?t=...` in the browser. The page is served to this computer only and every
request needs the token in that address. Every button runs the same code as the matching `bh` command; the
command and its output appear in the **Activity** bar at the bottom of the page. Drag the top edge of the
open Activity bar to make it taller or shorter (arrow keys when the edge has the focus, double-click for the
default size); the page remembers the size.

### Robots tab: one card per robot

(All registered robots, or the ones named on the command line.) Right of the tabs, one line sums up all
robots: how many run, are not running or are offline, and how many problems and warnings they have.

The page stays grey while everything is normal; colour marks only what needs attention, and always comes
with an icon and a word (the Help tab has the key):

| Looks like | Means |
|---|---|
| red, octagon with a cross | **problem** - act now (crashed, battery below the warning level, joint very hot) |
| amber, triangle | **warning** - check soon (software not running, battery only for short tests, warm joint, disk nearly full, crash dump) |
| blue, circle with "i" | **information** - nothing to fix (laptop guard armed, the robot is claimed by you) |
| green, tick | the robot software runs |

The coloured top edge of a card shows its worst entry.

Above the cards, three steps explain how sharing works (claim, ask, hand back) until you press **Got it**;
clicking "you are NAME" at the top of the page shows them again.

Each card, from top to bottom:

- the robot's **name**, its software state (running / stopped / crashed / offline - hover it for what to
  do), robot type and, at the right, **Stop** (red outline; **Start** while the software is not running).
  Stop is a hard stop and never asks. Below: address, hardware, backups on the robot;
- **who is using it**, a bar with a button: **Free** (grey, **Claim**), **Yours** (blue, with your note and
  when the claim ends, **Release**) or **NAME is using it** (amber, with their note, **Take over**). The
  claim is kept on the robot, so every teammate's dashboard shows the same ([sharing-robots.md](sharing-robots.md));
- **the back LED**, drawn on a small robot figure in the colour the robot software reports, blinking like
  the real one, with its meaning underneath (hover for the whole text). The robot software writes its LED
  state next to its status file (`InternalStateProvider`, at most once a second, and only states that
  lasted half a second, so the short test-mode flashes do not show). A robot whose software is older than
  this shows "LED unknown" until it is deployed again; a robot whose software is stopped shows a grey LED;
- five tiles: battery, hottest joint, robot computer, wireless, free disk - grey while normal, amber or red
  with an icon when not (hover a tile for its limits). The robot software reports the battery while it runs;
  while it is stopped, the battery is read from Booster's own battery service about once a minute and the
  tile says "battery (Booster)" - the first reading appears a few seconds after the page starts;
- problems and warnings that the state badge and the guard row do not already show (crash report, battery
  low, joint hot, disk nearly full, calibration not pulled), worst first. Click one for what to do;
- the **Guard** row, for a robot deployed with the laptop guard: its state (**may move** in green or
  **stands still** in grey), **RUN** / **STOP** and **End**. RUN starts a heartbeat for this robot only and
  lets it move; STOP makes it stand still (a soft stop: it does not go limp); End stops sending (the robot
  stays stopped). The heartbeat goes only to robots you pressed RUN for, so the page never overrides the
  guard of a robot somebody else is testing. Closing the page or a sleeping computer means STOP;
- four buttons, each with a few words underneath that say what it does. Every button that changes the
  robot opens a dialog first; the dialogs of everything that makes the robot go limp ask you to confirm
  that it sits or is held, and if somebody else claimed the robot you must confirm that you asked them.
  **Cancel** is on the left and has the focus:

| Button | Underneath | Same as | Note |
|---|---|---|---|
| **Deploy** | build + copy all | `bh deploy` | build, scenario (with the walk speed it allows), player number, LED mode, guard, back up first, dry run |
| **Policies** | send .onnx only | `bh policy push` | **Compare** lists what differs; **Send and restart** copies and restarts |
| **Restart** | limp briefly | `bh restart` | |
| **More** | drive, back up, logs | | opens a menu, each entry with a line on what it does: |

| More → | Same as | Note |
|---|---|---|
| **Hold STAND** (practice only) | - | what holding STAND on the robot for 2 s does |
| **Drive with gamepad** | - | the gamepad buttons, with the robot's LED live (it blinks slowly while the gamepad is in control) |
| **Penalize** / **Unpenalize** (with `--gc`) | test GameController | |
| **Back up**, **Restore** | `bh robot backup`, `bh robot restore` | Restore lists the backups on the robot |
| **Check robot**, **Log**, **Crash report** | `bh robot scan`, `bh logs`, `bh logs --crash` | read only |

Hovering any button shows the matching `bh` command and what it does.

- **Details** - team and player number, scenario, location, load, memory, uptime, when it was deployed, and
  the last lines of its log, including everything it said aloud.

### Policies tab

1. Drop `.onnx` files onto the page (they are kept in `Build/Dash/uploads`). Each file shows its input and
   output size and, per robot type, which slots it fits.
2. Drag a file onto a slot (walk, kick, kick-soft, getup, getup-fast) and choose **the simulator only**,
   **the real robots** or **both** - this is `bh policy use`, so a file that does not fit is refused before
   anything changes. The slot shows which file the real robots and the simulator use.
3. **Try in the simulator** starts `bh sim`.
4. **Send to NAME** is `bh policy push` (or deploy).

The last tile, **New slot**, explains what a new kind of policy (for example intercept) needs: C++ code,
configuration and one line in `Util/bh/bhtool/policy.py`; then the slot appears on this tab by itself
([policies.md](policies.md#adding-a-slot-a-new-kind-of-policy-eg-intercept)).

Using a file changes `Config/` in your checkout, exactly like the command; undo with git.

### Help tab

The LED colours with blinking examples, what the colours of the page mean, and short answers to "how do I...".

**The game** (top of the page, from GameController packets): state, half, remaining time, score, set play,
which GameController is sending, whether it was lost, and whether the own team is in that game. With a
GameController running each robot card also shows its penalty and the remaining penalty time.

**STOP ALL** (top right, red on a yellow ring, always visible - also while a dialog is open) ends the robot
software on every robot (they go limp), sets every guard heartbeat to STOP and sets the test
GameController's stop flag. It does not ask. With `--passive` it is not shown: that page never contacts the
robots, a banner says so.

If the page loses contact with `bh dash`, a red banner says so: the page is then out of date and its
buttons do nothing.

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
| `--port N`, `--no-browser` | where to serve (default 8089, with `--practice` 8090); do not open the browser |
| `--practice` | practice on simulated robots - next section |

### Practising on simulated robots

```bash
./bh dash --practice
```

starts SimRobot and the dashboard with the practice robots **Sim1, Sim2, Sim3**. Their bodies are the
simulated robots 1-3 of SimRobot's first team; everything else about them (software, configuration, claims,
backups, logs) is a stand-in in `Build/Practice`, under the loopback addresses 127.0.0.11, .12, .13. So
every button runs its real code, and **nothing can reach a real robot**. Your checkout is not changed.

What happens to a practice robot happens to its simulated body, as it would to a real one:

| You do | The simulated robot |
|---|---|
| **Stop**, **STOP ALL**, **More → Restore** (software stopped) | goes limp and falls |
| **Start**, **Restart**, **Deploy** (software started again) | stays limp, like a real robot after a restart: the LED blinks blue |
| **More → Hold STAND** (practice only; on a real robot: set it on its feet, hold STAND for 2 s) | stands up |
| **Deploy** with "Arm the laptop guard", then the Guard row **RUN** / **STOP** | stands still until RUN, moves while the page sends RUN, stands still again after STOP, closing the page or 1 s without RUN |
| test GameController buttons (initial ... finished, goals, **Penalize**, **FREEZE**) | the simulator's GameController; FREEZE makes the robots stand still |

The cards show the simulated robot's real LED, its position and whether it has fallen. Sim3 is claimed by
"Kim (practice teammate)", so you can practise asking before you deploy, **Take over**, **More → Back up** and
**More → Restore**. Policies you use on the Policies tab change only the practice copy; the running simulator
keeps its own.

| Option | |
|---|---|
| `--model k1` / `t1` | robot type (default k1) |
| `--scene NAME` | SimRobot game scene (default `GameFast3v3` for the K1, `GameFast` for the T1) |
| `--count N` | number of practice robots (default 3) |
| `--sim-config C` | which SimRobot build to use (default Develop; build it with `bh build sim`) |
| `--headless` | SimRobot without a window, e.g. on a computer you reach by ssh |

On a computer you reach over ssh, run it with `--headless --no-browser` and forward the page:
`ssh -L 8090:127.0.0.1:8090 COMPUTER`, then open the printed address on your own computer.

Ctrl+C ends the dashboard and SimRobot. Closing SimRobot's window ends the practice robots' bodies; the page
then says so.

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
