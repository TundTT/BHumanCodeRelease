# Sharing robots with other people

A deploy replaces the robot's software and its whole configuration, and the robot has one of each. So two
people cannot run their own code on the same robot at the same time. What can be made easy is taking turns:
say who is using a robot, and leave it the way you found it.

| I want to... | Command | Dashboard |
|---|---|---|
| tell others I am using a robot | `bh robot claim Alice --note "walk test"` | **Claim** in the bar under the robot's name (**Take over** if somebody else has it) |
| keep what is on it so I can put it back | `bh robot backup Alice` (or `bh deploy Alice --backup`) | **More → Back up**, or tick it in **Deploy** |
| put it back | `bh robot restore Alice` | **More → Restore** |
| say that I am done | `bh robot release Alice` | **Release** |
| see who uses which robot | `bh status` (column "in use by") | the bar under each robot's name: **Free**, **Yours** or **NAME is using it** |

Everybody runs their own `bh dash` (or `bh` commands) on their own laptop. Nothing is shared between the
laptops: the claim is a small file on the robot itself (`~/.bh-claim`), so every dashboard and every `bh`
command that reads the robot shows the same name. The dashboard shows the three steps (claim, ask, hand back)
above the robot cards until you press **Got it**; clicking "you are NAME" at the top shows them again.

## A turn on a robot somebody else set up

```bash
./bh robot claim Alice --note "intercept test" --hours 2
```

```bash
./bh deploy Alice --backup --guard
```

Test. Then hand the robot back:

```bash
./bh robot restore Alice
```

```bash
./bh start Alice
```

```bash
./bh robot release Alice
```

## Claims

A claim is a note on the robot (`~/.bh-claim`): who, since when, until when, and what for. Your name is
taken from `--as`, the environment variable `BH_USER`, your git user name, or your login name, in that order.

- `bh deploy`, `bh policy push` and `bh robot restore` refuse a robot that **somebody else** has claimed
  and name that person. Ask them; then repeat with `--ignore-claim`. The dashboard asks you to tick "I
  asked them".
- Stopping always works (`bh stop`, STOP ALL, the red button) - a claim never stands between anybody and a
  stop.
- `bh match` only warns: a forgotten claim must never keep a robot out of a game.
- A claim ends by itself after `--hours` (default 8), so a forgotten one blocks nobody for long. The times
  are taken from the robot's clock.
- `--force` takes over or removes somebody else's claim. Only do that after asking.
- A claim is a convention between team members, not a lock: anybody with the team key can still ssh into
  the robot.

## Backups

`bh robot backup NAME [--label TEXT]` stores what a deploy replaces in `~/.bh-backups/<date-time>.tar` **on
the robot**: `~/bin`, `~/Config` (with calibration and inference engines), and the `bhuman` service files of
user `booster`, plus whether the service was enabled. It checks the free space first (1 GB must remain) and
runs at low priority; the robot keeps running.

`bh robot backups NAME` lists them (`--delete ID` removes one). `bh robot restore NAME [ID]` (default: the
newest)

1. checks that the backup can be read and that there is space,
2. stops the robot software - **the robot goes limp**, so it asks first,
3. moves the current files aside, unpacks the backup, and puts the old files back if that fails,
4. restores whether the service was enabled,

and leaves the software stopped: start it with `bh start NAME`.

Not in a backup: the logs, the vendor services that `bh robot install` switched off, and anything else
outside the home directory. Backups use space on the robot (Config with inference engines can be several
hundred MB); delete the ones nobody needs.

## Why not one folder per person on the robot?

The robot software uses the camera, the motors and the GPU exclusively, so only one copy can run at a time
anyway, and the configuration search path (`Config/Robots/<name>/` first) is shared by all of them. Taking
turns with a claim and a backup gives the same result without changing how the robot is set up.
