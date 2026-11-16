# What the motion policies are held to

For the people who train the walk, kick and get-up policies: what the rest of the software asks of them, what
the simulator showed it actually uses, and where there is room to be better. Numbers marked **(config)** are
read from the files the robot reads and can be listed at any time with `bh policy limits`; numbers marked
**(measured)** come from recorded simulated games (`bh eval --stats`, `bh stats`). Everything else is stated
as an assumption.

How to read this: a *requirement* is something the behaviour code assumes, so a policy that falls short
breaks it. A *limit* is the most the behaviour ever asks for, so a faster policy gains nothing until the
configuration is raised too. Both are said for each item.

## 1. Walking

### The request the policy gets (config)

| | K1 | what it means |
|---|---|---|
| fastest forward | 2000 mm/s in the simulator and in `CompetitionWalk`; **1000 mm/s with the `Default` scenario** | cap on the speed the behaviour may command |
| fastest sideways / backwards | 1500 / 1000 mm/s (sim, `CompetitionWalk`); 500 / 500 (`Default`) | |
| fastest turning | 120 deg/s (fast), 90 deg/s (normal), 54 deg/s (slow, 90 in `CompetitionWalk`) | |
| step size may grow by | 200 mm/s per step in `CompetitionWalk`, 60 in the simulator, 20 in `Default` | how quickly it may accelerate from the last step |
| gait frequency | 1.5 Hz, the policy may move it by +-0.5 Hz | |
| interface | 344 inputs, 13 outputs (T1: 365, 14); 50 Hz; at least 20 ms between requests | observation = 10 frames of history x 32 values + 24 extra values; output = joint offsets (clipped +-2) + gait frequency |

Three things follow that are easy to miss:

- **Which scenario is deployed decides the walking speed the robot may use.** Our competition deploys use
  `CompetitionWalk`, so the numbers to train for are its column (2000 / 1500 mm/s, step growth 200). The default
  scenario caps a robot at half the speed the simulator plays with, which is only a risk when someone forgets
  `--scenario CompetitionWalk`. `bh deploy` prints the cap it deployed and warns when
  another scenario would allow more. A policy that is trained for 2000 mm/s is never asked for it with the
  default scenario.
- **The simulator is not `CompetitionWalk`.** It uses `Config/Robots/SimulatedK1` (acceleration 60, slow turning
  54 deg/s). Simulated results say how much walking matters, not how fast the competition configuration is.
- **The observation is built in C++ and checked only by `ASSERT`**, which release builds drop. A new policy
  needs the same input and output sizes (`bh policy check` before every deploy) and the same meaning of every
  input; a mismatch of meaning produces a worse walk without any error.

### What the policy is asked to do in a game (measured, preliminary)

From a two-game smoke run (386 s of play each, simulator, 5 vs 5, `bh stats`):

| per robot | value |
|---|---|
| speed, median | 210-230 mm/s (a robot stands or walks slowly most of the time) |
| speed, 95th / 99th percentile | 1200 / 1650 mm/s (so fast walking up to the 2000 mm/s cap does occur) |
| turning, 95th percentile | 64-68 deg/s |
| standing still (< 100 mm/s) | about 40 % of the time |
| distance walked | about 138 m in 386 s |

Reading: the average is low; what matters is how fast and how stably the robot gets to the ball when it has to.
The 99th percentile is the figure to push first. These are two games; replace them with the numbers of the
long runs in section 5 before using them as a target.

### Criteria to train towards

| goal | criterion | why |
|---|---|---|
| keep what works | no more falls than the current policy: **0.1 falls per robot per 6.5 minutes** in the simulator | a fall costs the get-up time below and the ball |
| tracking | reach the commanded speed within the step-size limit above, in both directions | the behaviour plans with those limits |
| top speed | forward 2000 mm/s sustained is the highest the configuration can ask for; beyond that raise `fastWalkSpeed` first | otherwise the extra speed is never used |
| turn rate | 120 deg/s in the fast walk; the slow walk (used near the ball) is limited to 54 deg/s in the simulator | circling the ball is slower than approaching it |
| stability | start, stop and turn on the spot without stumbling; recover from a push by another robot | contact with other robots is common near the ball (not yet measured as a cause of falls) |

## 2. Kicking

### The request the policy gets (config)

The behaviour picks a kick from `Config/Scenarios/Default/kickInfo.cfg`, and the kick policy receives the ball
speed to produce. The ball slows down by 0.30 m/s^2, so it rolls v^2 / 0.6 after leaving the foot.

| kick | ball speed (mm/s) | rolls (mm) | used for |
|---|---|---|---|
| `rlKickDynamic` | 500 - 3400 | 417 - 19267 | the speed follows the distance to the target (`velocityForDistance`) |
| `rlKickDynamicOld` | 100 - 1700 | 17 - 4817 | the older, softer kick policy |
| `rlKickStrong` | 5400 | 48600 | fixed speed, the hardest kick |
| `walkForwardStealBall*` | 490 | 400 | moving the ball while walking, no real kick |

Field: the M field is 14 m x 9 m (goal 2.5 m wide, from `fieldDimensions.cfg`), so one kick of 3400 mm/s (19 m)
crosses the whole field. The
ball has a radius of 95 mm and a mass of 0.3 kg.

Requirements:

- the ball must leave with the **requested speed**, not just fast: the behaviour computes the distance it wants and
  asks for the speed that gives it. A kick that is too strong sends the ball out of the field, one that is too
  weak is intercepted.
- the **direction** must be within about 5 degrees of the request (assumption used by the pass and shot
  decisions; it is not checked anywhere in code).
- the kick must **not make the robot fall** and must leave it able to walk on at once.

### What kicks look like in a game (measured, preliminary)

Two games, all kicks counted when the ball speed jumps by at least 600 mm/s to at least 800 mm/s near a robot:

| ball speed after the kick (mm/s) | share |
|---|---|
| < 1000 | 0-1 % |
| 1000 - 2000 | 20 % |
| 2000 - 3000 | 25 % |
| > 3000 | 40-55 % |

The median is 2800-3400 mm/s, the 90th percentile 5600 mm/s, the maximum about 6200 mm/s. About 45 kicks per team
and game. So most kicks use the upper half of the range, and a good part reaches the strong kick; the number to
**keep** is the maximum, and the number to **improve** is how well the achieved speed matches the requested
one (this is not measured yet: it needs the requested speed in the recording).

## 3. Getting up

### Criteria (config)

| | value |
|---|---|
| attempts before the robot asks for help | 3 |
| must be standing taller than | 430 mm torso height |
| slow policy, lying on the front | 3.5 s (the table ends at 5250 ms, divided by the speed factor 1.5) |
| slow policy, lying on the back | 4.0 s (6000 ms / 1.5) |
| fast policy | used on the real robot; its first try is allowed 1.5 s x speed factor |

**The simulator does not use the fast get-up** (`GetUpEngine.cpp` switches it off for simulated robots; a test in
`Util/bh/tests/test_units.py` keeps this document honest). Get-up times seen in the simulator, about 6.4 s from
fall to standing in the smoke run, therefore describe the slow policy plus the fall itself and are an upper bound
for the real robot, not a measurement of it.

Criteria to train towards: stand in under 3 s from the front and from the back on the real robot (the current
slow table is 3.5-4 s), succeed at the first attempt on carpet and on the competition turf, and never get up
into another robot.

## 4. Falling and balance

The detector that decides a robot has fallen is `FallDownStateProvider` (`Config/Robots/Default/K1`): it looks at
the torso angle (55 deg), the centre of mass against the foot (150 mm) and the gyro rate (50 deg/s to count as
stable again). A policy that sways more but stays inside these thresholds is not counted as falling; one that
passes them triggers the fall engine and the get-up.

## 5. How much each capability is worth

The question is how much a better walk or kick would add to the score. We measure it the other way round: take
a capability away from one team and play the unchanged team against it (`bh eval --variant ... --stats ...`,
sides swapped, judged at two standard errors; one game's goal difference scatters by 3.7 goals, so 40 games
resolve about 1.1 goals per game).

| handicap for one team | question | result |
|---|---|---|
| fast walk 20 % slower | how much is top speed worth | pending (running) |
| kick speed capped at 2000 mm/s | how much is a hard kick worth | pending |
| slow turning 33 % slower | how much is turning near the ball worth | pending |
| competition walk parameters (acceleration 200, turning 90) against the simulator's | what the configuration alone adds | pending |

Until those finish, the earlier A/B runs say what does *not* decide games here: shooting versus passing
decisions, the walk offset pairing and the fixed formations (all within noise at 40 games each). The handicap
results will be entered here with the numbers, and the sentence "X is worth Y goals per game" only where the
confidence interval allows it.

## 6. Ways to go beyond the current limits

Ordered by what is cheapest to try; each is a configuration change you can compare with `bh eval` before any
robot is involved.

1. **Check the deployed scenario every time.** With `Default` the policy is capped at 1000 mm/s; competition
   deploys must pass `--scenario CompetitionWalk` (the deploy output shows the cap it used).
2. **Raise `fastWalkSpeed` above 2000 mm/s** only together with a policy trained for it and a larger
   `maxAcceleration`; otherwise the new top speed is never reached.
3. **Use the strong kick's speed range** (5400 mm/s) for the dynamic kick: `rlKickDynamic` stops at 3400 mm/s,
   which leaves long clearances and shots without more speed. The kick policy's input is clamped by
   `kickVelocityRange` in `walkingEngine.cfg`.
4. **Make the fast get-up usable in the simulator**, so that falls can be evaluated at the real speed; today the
   simulator is pessimistic about falls.
5. **Record the requested kick speed next to the achieved one**, to measure the policy's accuracy directly.

## How to reproduce and extend this

```bash
bh policy limits                     # the configuration numbers above
bh eval --games 40 --variant WalkingEngine=my/walkingEngine.cfg --stats Build/Stats/mine
bh stats Build/Stats/mine            # speeds, falls, get-ups, kicks of both teams
```

`bh eval --stats PREFIX` makes the simulator write the ground truth of every game to `PREFIX_<pid>.csv` (10 Hz;
nothing in the robot code is changed). `bh stats PREFIX` reads it. See [simulation.md](simulation.md) for the
statistics behind the comparisons, and [policies.md](policies.md) for how to put a new policy into the simulator
or on a robot.

What the simulator cannot tell you: it uses perfect perception (`GameFast`), a slower get-up than the robot, a
different walk configuration than the competition one, and simplified contacts. Treat simulated differences as
evidence about the behaviour's use of a capability, and verify motion changes on a robot, held or on a soft
surface, under `bh guard`.
