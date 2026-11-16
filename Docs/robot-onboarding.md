# Bringing this code onto a robot

Works for a robot fresh from the factory and for a robot that already runs somebody else's software.
Supported hardware: Booster **K1** and **T1**. The code is tested with robot firmware **1.7.1**; firmware
1.8.0 is known to misbehave (`bh robot scan` warns about it).

## The short way

Robot switched on, connected to your computer by network cable, your computer has an address like
`192.168.10.50` (netmask `255.255.255.0`). A factory robot answers at `192.168.10.102`.

```bash
./bh robot onboard Alice --id 14
```

`--id` is a number from 1 to 254 that you choose and that must be unique per robot; it becomes the last
number of the robot's addresses. Add `--model t1` for a T1. The command runs four steps and stops with an
explanation if one fails:

1. **Register** - creates `Config/Robots/Alice/network.cfg` and an entry in `Config/Robots/robots.cfg`
   (cable address `192.168.<team>.<id>`, wireless `10.0.<team>.<id>`, team number from `Config/settings.cfg`).
2. **Scan** - a read-only look at what is on the robot (see below). You are asked for the password of the
   robot's user `booster`, because the team's ssh key is not installed yet.
3. **Install** - runs `Install/installRobot`: installs the team ssh key, the start scripts and the
   `bhuman` service, sets the robot's name and addresses, and switches off the vendor services that compete
   for camera, GPU, audio and buttons. The vendor configuration is backed up on the robot first.
4. **Reboot.**

When the robot is back, it answers at its new address:

```bash
./bh robot scan Alice
```

```bash
./bh deploy Alice --guard
```

Then calibrate it ([calibration.md](calibration.md)) before it plays.

## What the scan looks for

`bh robot scan <name | address | factory>` changes nothing. It reports:

| Finding | Why it matters | What to do |
|---|---|---|
| start scripts not installed | the robot cannot run this code yet | `bh robot install NAME` |
| vendor services still enabled (camera, vision, audio, ...) | they hold the camera and GPU, so perception gets slow or fails | `bh robot install NAME` switches them off |
| ONNX Runtime missing / without TensorRT or CUDA | deploying stops with "ONNXruntime not found", or the networks run on the CPU | install the robot build of ONNX Runtime for user `booster` (see the upstream setup guide) |
| foreign autostart services of user `booster` | another team's code starts at boot and fights for the motors | `bh robot quarantine NAME --all` |
| crontab entries | same | `bh robot quarantine NAME --cron` |
| running containers, unknown busy processes | they take CPU/GPU from the robot software | stop or remove them by hand (`bh ssh NAME`) |
| firmware 1.8.x | known to misbehave with this code | use firmware 1.7.1 |

Use `--password` for a robot that does not have the team key yet.

## Switching off other software without destroying it

```bash
./bh robot quarantine Alice --all --cron --dry-run
```

```bash
./bh robot quarantine Alice --all --cron
```

```bash
./bh robot quarantine Alice --undo
```

Quarantine stops and disables the named user services (`--unit NAME.service`, or `--all` for everything the
scan flags) and saves and removes the crontab. Nothing is deleted: the list of services and the old crontab
are kept in `~/.bh-quarantine` on the robot, and `--undo` restores both. System services and this code's
own service cannot be quarantined.

## The single steps

| Command | What it does |
|---|---|
| `bh robot list` | registered robots and how they are reachable |
| `bh robot add NAME --id N [--model t1] [--team T]` | register only |
| `bh robot install NAME` | install on a robot at the factory address |
| `bh robot install NAME --update` | repeat the installation on a K1 at its own address |
| `bh robot install NAME --uninstall` | restore the vendor setup, keep this code's files |
| `bh robot install NAME --uninstall-all` | restore the vendor setup and delete this code's files |
| `bh robot remove NAME` | forget the robot on this computer (deletes its calibration files!) |
| `bh reboot NAME`, `bh shutdown NAME` | power control |

## Good to know

- The installer needs `rsync` and a Linux shell. On Windows it runs inside WSL (`bh doctor` checks this).
- A robot keeps its registration in the repository. Commit `Config/Robots/<name>/` and `robots.cfg` so that
  every team member's computer knows the robot.
- Deploying replaces the whole `Config` directory on the robot. `bh deploy` first rescues what only exists
  on the robot (a saved calibration, inference engines the robot built for itself);
  the plain `Make/Common/deploy` script does not.
