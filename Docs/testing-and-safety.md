# Deploying, testing and staying in control

## Deploy

```bash
./bh deploy Alice
```

```bash
./bh deploy Alice:2 Bob:3 --guard --led test
```

`name:number` also sets the player number. What happens, in this order:

1. **Policy check** - every walk/kick/get-up network must exist and match the sizes in its configuration.
   The robot itself only checks this in Debug/Develop builds ([policies.md](policies.md)).
2. **Build** (Develop unless `-c Release`/`-c Debug`; `--no-build` uses the existing binary and warns if the
   sources are newer).
3. **Rescue** - a calibration that was saved on the robot and inference engines the robot built for its own
   hardware are copied into the repository, because the next step would delete them. A replaced local
   calibration is kept in `Config/Robots/<name>/.calibration-backup/`.
4. **Copy** - the robot software stops (**the robot goes limp**) and binary and configuration are copied.
5. **Verify** - the checksum of the binary on the robot and the team/player/scenario/location settings are
   compared with what was meant to be sent. A robot that failed is named; the others continue.
6. **Start** (`--no-restart` skips it).

Useful options: `--team`, `--scenario`, `--location`, `--profile` (wireless profile from `Install/Profiles`),
`--volume`, `--color`, `--goalie-color`, `--magic`, `--delete-logs`, `--dry-run` (all checks, nothing copied).

After the start the robot is limp (back LED blinks blue). To stand it up (K1):

1. Set it on its feet and hold it.
2. Hold **STAND for 2 seconds**: it stiffens and holds a ready pose (LED magenta, it says "Prepare").
3. Hold **WALK for 2 seconds** while it is upright and still: it says "Walk" and is active. If the LED
   blinks fast instead, it is tilted or moving - steady it and hold WALK again.

Without a GameController it is now *penalized* (LED red, stands still); each short press of STAND or WALK
switches between *playing* (LED green) and *penalized*.

**F1 makes it limp again at any time.** After F1 the robot ignores STAND and WALK until you hold **F1 for about
2 seconds** - it says "Body ready" - then stand it up as above.

## Layers of control

From "always works" to "most comfortable":

| Layer | How | Robot reaction | Needs |
|---|---|---|---|
| F1 button | press it on the robot | limp at once | nothing |
| Hard stop | `bh stop` (all robots) or `bh stop Alice`; **STOP ALL** in `bh dash` | software ends, limp | network |
| Laptop guard | `bh deploy Alice --guard`, then `bh guard Alice` | stands still while not released | network |
| GameController stop flag | `stop` in `bh gc test`, FREEZE in `bh dash --gc`, or the official controller | stands still | network |
| Penalize | short button press, `pen <n>` in `bh gc test`, Penalize in the dashboard | stands still | - |

A limp robot falls. Before a hard stop, hold the robot if you can.

## The laptop guard (dead-man switch)

Use it whenever a new behaviour, policy or parameter runs on a real robot for the first time.

```bash
./bh deploy Alice --guard
```

```bash
./bh guard Alice
```

`bh guard` starts in STOP. ENTER switches between RUN and STOP. The robot is allowed to move only while it
receives the guard's "run" heartbeat. It stops by itself within one second if

- you press ENTER or Ctrl+C,
- the laptop sleeps, crashes or runs out of battery,
- the network connection drops.

"Stops" means it stands still and keeps its balance; it does not go limp. The exception: a robot that is
falling or getting up at that moment relaxes on the ground and stays down until released.

How it works: a robot deployed with `--guard` gets a robot-side thread configuration
(`Config/Robots/<name>/threads.cfg` on the robot only) that enables the existing `EmergencyButtonProvider`.
It listens on UDP port 9910. The same file enables `RemoteDriveProvider` (UDP 9911), with which the
dashboard's **Drive** dialog walks the robot by keyboard ([dashboard.md](dashboard.md)) - only while the
guard says RUN. Nothing in the repository changes, and **the next deploy without `--guard`
removes it** - so match deployments are never guarded (`bh match` also makes sure of that).
`bh status` and the dashboard show "laptop guard is armed" for such a robot.

Limits you should know:

- The guard is implemented by the behaviour (`HandleStoppedState` in `optionPlaySoccer.cfg`). The Default
  scenario has it; `bh deploy --guard` refuses scenarios that do not (currently AnyPlaceDemo).
- It is a software stop over the network. It does not replace the F1 button or a person next to the robot.
- A guarded robot that nobody releases never moves. If a robot "does nothing", check `bh status`.

The dashboard offers the same switch as buttons: **RUN** / **STOP** on the card of a robot deployed with
the guard starts a heartbeat for that robot only (`bh dash --guard`: for all robots at once). There the
robots also stop when the browser tab is closed.

## Watching a test

```bash
./bh status
```

```bash
./bh logs Alice -f
```

```bash
./bh dash --gc --guard
```

- `bh status` - one line per robot: software state, battery, hottest joint, computer temperature, deployed
  team/player/scenario, and warnings (crash dump, low battery, unsaved calibration, armed guard).
  `--json` for scripts.
- `bh logs NAME` - the robot's log, including everything it says aloud ("Saying ..."). `-f` follows,
  `--crash` shows the crash dump of the last run. `--download [--delete]` fetches all log files into
  `Config/Logs/<date>/<name>/`; the robot software is stopped during the transfer (**the robot goes limp**)
  and started again afterwards, so the command asks first.
- `bh dash` - the same and more in the browser ([dashboard.md](dashboard.md)).
- The LED on the back shows the robot's state ([leds.md](leds.md)).

## Testing a change step by step

1. `bh test` and, for C++ changes, a build (`bh build`).
2. Simulator: `bh sim`; for behaviour, parameter or policy changes measure with `bh eval` - many games, and
   where possible the new variant against the old one in the same games (`--con`, see
   [simulation.md](simulation.md)).
3. One robot, guarded, on a soft surface or held: `bh deploy Alice --guard`, `bh guard Alice`.
4. With a practice referee: `bh gc test` or `bh dash --gc` ([game-controller.md](game-controller.md)).
5. Only then the match build: `bh match ...` ([match-day.md](match-day.md)).
