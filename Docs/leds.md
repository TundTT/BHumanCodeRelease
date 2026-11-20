# The LED on the robot's back

The K1 has a coloured LED on its back (the T1 has none that this code controls). It always shows the state
the robot is in; a mode decides what it shows in addition.

## Colours (both modes)

| LED | Meaning | What to do |
|---|---|---|
| blue, blinking | limp (unstiff) | hold STAND for 2 s to make it stiffen |
| magenta | stiff, ready pose ("Prepare") | hold WALK for 2 s while it is upright and still |
| white | standing, waiting: *initial* or *finished* | |
| light blue | *ready* - walking to its position | |
| yellow | *set* - waiting for the kick-off | |
| green | *playing* | |
| red | penalized (by the referee or by a button press) | |
| orange | calibration mode | [calibration.md](calibration.md) |
| any colour, slow blinking | a gamepad or the dashboard's Drive dialog controls the robot | |
| any colour, fast blinking | the robot cannot do what was requested yet, e.g. it is still standing up | wait; if it stays, look at `bh logs NAME` |

## Match mode

The colour never changes while the robot may move. One addition, before and after the game:

| LED | Meaning | What to do |
|---|---|---|
| **white, blinking** | standing in *initial* or *finished* and **no GameController heard** | `bh gc doctor` ([game-controller.md](game-controller.md)) |
| white, steady | the GameController is heard | ready for the game |

So one look at the robots standing at the touchline tells you whether every one of them is connected.

## Test mode

Short flashes (0.2 s, every 2 s) report what you cannot see from outside. Between the flashes the LED shows
the state colour.

| Flash | Meaning |
|---|---|
| blue | the robot is playing but has not seen the ball for 3 s |
| red | battery below 25 % or a joint is very hot |
| dark (LED off for a moment) | the same as the red flash while the LED itself is red or orange |

The two flashes use different halves of the 2-second period, so a robot with both problems shows both.
There are no flashes while the robot is limp, standing up, or blinking for one of the reasons above.

## Choosing the mode

| How | Effect |
|---|---|
| nothing | `automatic`: a robot running a **Release** build uses match mode, Develop and Debug builds use test mode |
| `bh deploy NAME --led test` / `--led match` / `--led auto` | sets the mode on that robot for this deployment |
| `bh match ...` | always match mode |
| `mode = ...;` in `Config/Scenarios/Default/ledHandler.cfg` | the default for all robots |

The same file holds the thresholds: `ballLostTime` (ms), `lowBatteryLevel` (%), `blipPeriod` and
`blipDuration` (ms; 0 switches the flashes off). The dashboard (and `bh status --json`) shows the mode a robot
was deployed with, and the dashboard has this legend built in.

## Why match mode is so quiet

Every change of the LED colour is a remote procedure call to the robot's firmware, made by the motion
thread - the thread that also runs the walk policy. A steady colour costs nothing; a blinking LED makes
that call every 0.4 s, a fast blinking one every 0.08 s. In match mode nothing is added while the robot
plays: the only additional blinking is in states in which the robot stands still. Test mode adds at most
four calls per 2 seconds, and only while a problem is being reported.

In Develop and Debug builds a failed LED call ends the robot software (an existing check in
`BoosterProvider`); in Release builds the result is ignored.

## For developers

The colours are computed in `Src/Modules/BehaviorControl/LEDHandler/LEDHandler.cpp` and applied in
`Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp`. This page, the legend in
`Util/bh/bhtool/dash.py` (`LED_LEGEND`, `LED_COLOURS`) and the code must say the same thing - change them together.

`InternalStateProvider` reports the LED state (the three `LEDRequest::State` values of red, green, blue) in
`ledState.txt` next to the status file; `bh status --json` and the dashboard read it. It reports only states
that lasted 0.5 s, so the test-mode flashes do not appear there. In SimRobot it writes nothing, unless
`bh dash --practice` sets `BH_PRACTICE_DIR`: then each simulated robot writes to
`<BH_PRACTICE_DIR>/<team number>-<player number>/` ([dashboard.md](dashboard.md#practising-on-simulated-robots)).
