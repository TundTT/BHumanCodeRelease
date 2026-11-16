# Testing in the simulator

## Start a game

```bash
./bh sim
```

```bash
./bh sim --list
```

```bash
./bh sim Game --model t1
```

`bh sim [scene]` builds SimRobot if it is missing (`--build` forces a rebuild, `-c Release` picks another
build configuration) and opens the scene. Without a name it opens `GameFast`.

| Scene | What it is | Use it for |
|---|---|---|
| `GameFast` | 5 vs 5, perception replaced by ground truth with noise | behaviour and strategy work (fast) |
| `GamePerceptOracle` | 5 vs 5, percepts from the simulator, real modelling | localisation and world-model work |
| `Game` | 5 vs 5 with rendered camera images and the real vision | vision changes (slow) |
| `Game11v11`, `GameFast11v11`, `GamePerceptOracle11v11` | the same on the large field (K1 only) | 11 vs 11 |
| `Game3v3M`, `GameFast3v3`, `GameFast3v3M` | small games (K1 only) | quick experiments |
| `Robot`, `RobotFast` | one robot on an empty field | motion, kicks, a single skill |
| `Replay` | replays a log file recorded on a robot | finding out what happened in a game |
| `Remote` | attaches to a real robot | use `bh connect NAME` ([dashboard.md](dashboard.md)) |

The simulated robots use the policies and parameters of `Config/Robots/SimulatedK1` / `SimulatedT1` first,
then the same files as the real robots.

## Controlling the game

Type into the SimRobot console (or use the shortcut):

| Command | Shortcut | Effect |
|---|---|---|
| `gc initial` / `ready` / `set` / `playing` / `finished` | Shift+Ctrl+I / R / S / P / F | game state |
| `gc kickOff firstTeam` / `secondTeam` | Shift+Ctrl+B / Y | who kicks off |
| `gc goal firstTeam` | | count a goal |
| `pr manual` / `pr none` | Shift+Ctrl+M / U | penalize / unpenalize the selected robot |
| `pr <penalty>` (`help pr` lists them) | Shift+Ctrl+G (pushing) | a GameController penalty for the selected robot |
| | Shift+Ctrl+K / L | penalty kick / pushing free kick for the selected robot's team |
| `mvb <x> <y> <z>` | | put the ball somewhere (mm) |
| `mv <x> <y> <z> [<rotX> <rotY> <rotZ>]` | | put the selected robot somewhere |
| `robot robot3` / `robot all` | double click a robot | choose which robots the following commands address |
| `dt off` / `dt on` | | run as fast as possible / in real time |
| `ar on` / `ar off` | | automatic referee |
| `help [pattern]` | | all commands |

`robot1` ... `robot5` are the first team, `robot21` ... `robot25` the second.

## Measuring instead of watching

One game says little. `bh eval` plays many games without a window, in parallel, and prints a table.

```bash
py -3 -m pip install psutil
```

```bash
./bh eval --games 8
```

It needs the Python package `psutil` once (first command; `python3 -m pip ...` on Linux/macOS). Every game is
two 10-minute halves of simulated time; `--workers N` limits the number of simulator processes,
`--scene`, `--model` and `-c` work as for `bh sim`. The result table has one line per half - kick-off team,
score of team A and B, remaining message budget, winner. The scores keep counting, so the line of the second
half is the result of the game; `bh eval` prints those final scores and the mean goal difference below the
table. It uses the existing `Util/TestRunner` and the simulator's `test game` command, and keeps its files
in `Build/Eval`.

On Linux without a desktop (ssh), `bh eval` starts the games on a virtual display by itself; that needs
`xvfb` (`sudo apt install xvfb`).

**How long it takes.** One simulator process uses about 20 CPU cores if they are free. Measured: a 24-core
PC plays 4 to 5 games per hour, however many run in parallel.

**How much to trust it.** Two identical teams, 20 games: team A minus team B was +0.75 goals per game
with a standard error of 0.82 - no advantage of a side - but single games ended anywhere between 3:9 and
9:3. The goal difference of one game scatters by about 3.7 goals. So after `n` games the mean is only known
to about `3.7 / sqrt(n)`: 20 games show effects above 1.6 goals per game, 50 games above 1 goal, 200 games
above half a goal. Fewer games than that prove nothing.

### Comparing two variants (A/B)

Both teams run the same binary. To find out whether other parameters are an improvement, give **one team**
those parameters and let the two play each other:

```bash
./bh eval --games 40 --variant WalkingEngine=my/walkingEngine.cfg
```

`--variant Module=FILE.cfg` takes a complete parameter file of that module (copy the one in use, e.g.
`Config/Robots/SimulatedK1/walkingEngine.cfg` - see the search path in [CLAUDE.md](../CLAUDE.md) - and change
what you want to try). It can be given several times. Parameter sets that do not belong to a module are
named the way the code registers them: `--variant behavior:PlayBall=my/rolePlayBall.cfg` for the role that
says `MODIFY("behavior:PlayBall", p)`. The variant plays half of the games as team A and
half as team B, and the summary says what the games allow to conclude:

```
Variant against the current configuration, 40 games:
  goals            212 : 187
  won/drawn/lost   21 / 5 / 14
  goal difference  +0.63 per game (standard error 0.58)
  verdict          no clear difference - noise is larger than the effect; play more games to be sure
```

`BETTER` and `WORSE` are only printed when the mean is more than two standard errors away from zero. A value
the module refuses stops the games with the module's `ASSERT` message (Develop build).

**Whole configuration files** - tactics, strategies, `threads.cfg`, anything that is found through the
configuration search path - are compared with a scenario of their own:

```bash
./bh eval --games 40 --scenario MyIdea
```

Create `Config/Scenarios/MyIdea/` and put only the files that differ into it, with the same relative path as
the original (for example `Behavior/Strategies/s5v5.cfg`); everything else is still found in the usual
places. One team then plays with that scenario instead of the one the scene names, again half of the games
as each team. Two things to know: files in `Config/Robots/SimulatedK1/` win over every scenario, and if the
scene's own scenario has files (GameFast uses `CompetitionWalk`), copy them into yours too, because your
scenario replaces it.

For anything else, `--con FILE.con` runs a SimRobot console script inside
`Config/Scenes` at the start of every game instead, for example to exchange a module for one team:

```
robot robot1 robot2 robot3 robot4 robot5
mr SomeRepresentation SomeOtherModule
robot all
```

Everything a console can do works in such a script (`mr`, `set module:...`, `mvo`/`mvb`). With `--con` the
change always belongs to the robots the script names; the summary is team A minus team B.

Changes that need a different binary (new C++ code) cannot be compared this way; make the new behaviour
switchable by a parameter, so that one binary can play both, and compare with `--variant`.

### Recording what happens (`--stats`, `bh stats`)

The score says who won, not why. `bh eval` can also record the ground truth of every game - where each robot
and the ball were, whether a robot stood upright, penalties - ten times per second:

```bash
./bh eval --games 40 --variant WalkingEngine=my/walkingEngine.cfg --stats Build/Stats/mine
```

```bash
./bh stats Build/Stats/mine
```

The simulator writes `Build/Stats/mine_<pid>.csv` per game process (an A/B run: `mine-varA_...` when the
variant played as team A, `mine-varB_...` as team B; `bh stats` joins both and prints the variant as "test"
next to the "reference"). The table has falls and time lying down, get-up time, walking speed (median, 95th and
99th percentile), turning, share of standing still, distance walked, number and strength of kicks (ball speed
jumps by at least 600 mm/s to at least 800 mm/s next to a robot), how often the ball is nearest to a team or
within 50 cm of a robot, and penalties. Nothing is recorded unless `--stats` is given, and no robot code is
involved (the recorder is in the simulator's game controller, `BH_STATS_FILE` / `BH_STATS_PERIOD` in the
environment). Use it to see *which* capability a change moved; do not conclude a cause from correlations of a
few games. What the numbers mean for the walk, kick and get-up policies is collected in
[motion-requirements.md](motion-requirements.md).

## Limits of the simulator

- `GameFast` does not run the vision; it cannot tell you anything about perception.
- The simulated robots use `Config/Robots/SimulatedK1`, not the competition walk configuration, and the
  get-up engine never uses its fast policy in the simulator (see [motion-requirements.md](motion-requirements.md)).
- The simulated robot is a model. Whether a walk or kick that works here works on the robot depends on how
  well the model matches *your* robot - see [calibration.md](calibration.md).
- The simulator cannot be built or started without the git submodules (`bh setup`) and a C++ toolchain
  (`bh doctor`).
