# Match day

## The day before

- [ ] Everything that will play is **committed**. `bh match` warns about uncommitted changes, because code
      that is not in git cannot be reproduced after the game.
- [ ] `bh test` passes; `bh policy check` passes.
- [ ] Every robot: `bh robot scan NAME` clean, `bh calib status` shows a camera calibration, and it has
      played a test game with the **Release** build: `bh deploy NAME:N -c Release`. Release differs from
      Develop: assertions are off and the LED is in match mode.
- [ ] A test game against the official GameController was played ([game-controller.md](game-controller.md)).
- [ ] The wireless profile of the field exists in `Install/Profiles` (`HSL_A`, `HSL_B`, ... are shipped;
      `NONE` means no wireless network).
- [ ] The field's location (`Config/Locations`) and the scenario are decided, e.g. `--location LField`.
- [ ] Batteries charged. The robot itself warns below 40 %.
- [ ] Licence: the use of this code was announced on the league's mailing list at least one month before
      the competition ([README.md](README.md#licence-obligations)).

## Before each game

Robots switched on, connected by cable or already in the field's network:

```bash
./bh match alice:1 bob:2 carol:3 dave:4 erin:5 --profile HSL_A --location LField
```

One command, in this order:

1. **Pre-flight.** Uncommitted changes and a missing `--profile` are reported. For every robot: reachable,
   battery, joint temperatures, disk space. A robot that is unreachable, has a battery below the level at
   which the robot itself warns, or a very hot joint **blocks** the deployment (`--force` overrides).
2. **Policy check** - a policy that does not fit its configuration would corrupt memory in a Release build.
3. **Release build.**
4. **Rescue** of calibration saved on the robots.
5. **Copy**, with the player numbers given, team number, scenario, location, wireless profile, colours
   (`--team`, `--scenario`, `--color`, `--goalie-color`, `--volume`, `--magic`, `--delete-logs`).
6. **Verify** on every robot that the binary and the settings are the ones meant. A robot that fails is
   named and the command ends with an error.
7. **Start**, LED in match mode, laptop guard removed.

`--dry-run` does steps 1 to 4 and stops before anything is copied to a robot - useful as a check an hour
earlier.

Then, on the field:

1. Hold **STAND for 2 seconds** on each robot. It stands up.
2. Look at the backs: **white steady** = the robot hears the GameController. **White blinking** = it does
   not - run `bh gc doctor` now, not after kick-off.
3. From here on the GameController decides. The robots walk to their positions in *ready* by themselves.
4. Close `bh dash`, `bh connect`, `bh logs -f` and every ssh session. During the game only
   `bh dash --passive` and `bh gc listen` are allowed to run - they only listen.

## During the game

| Situation | Do |
|---|---|
| referee asks to remove a robot | press F1 (limp), carry it out |
| robot back in | hold STAND for 2 s; the GameController handles the penalty |
| robot does not react any more | take it out; `bh status NAME` and `bh restart NAME` at the touchline, or switch it off and on |
| half time | `bh status` - batteries and temperatures; swap every battery it warns about |

## After the game

```bash
./bh logs alice --download
```

```bash
./bh calib status
```

Download the logs of every robot (the robot software stops during the transfer, so the robots should be
sitting). `Config/Logs/<date>/<robot>/` then holds what each robot saw and decided; replay with
`bh sim Replay`. `bh logs NAME --crash` shows the crash dump if a robot stopped during the game.

## If something goes wrong five minutes before kick-off

| Problem | Fastest way out |
|---|---|
| `bh match` blocks on a battery | change the battery; or `--force` if you know better |
| one robot fails to verify | repeat `bh match` for that robot alone: `bh match bob:2 --profile HSL_A ...` |
| build fails | `git stash` or `git checkout` the last version that played, `bh match` again |
| robot does not hear the GameController | `bh gc doctor`; usually the wrong profile or team number |
| robot has the wrong number | `bh match NAME:N ...` for that robot |

## Licence

Using this code in an official game has conditions (announcement before the competition, publication of the
derived software afterwards). See [README.md](README.md#licence-obligations) and
[License.md](../License.md).
