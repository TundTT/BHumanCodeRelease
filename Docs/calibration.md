# Calibration and the sim-to-real gap

Two different things make a real robot behave differently from the simulated one:

1. **What the robot sees is rotated** - the camera is never mounted exactly as in the model. Fixed by the
   camera calibration, per robot, in about five minutes. Without it distances to the ball and lines are
   wrong and localisation suffers.
2. **The body is not the model** - joint zero positions, masses, motor strength and delays differ. This is
   what makes a policy that walks in the simulator stumble on the robot. There is no automatic procedure;
   the levers are listed below.

## Where calibration lives

Robot-specific files are in `Config/Robots/<name>/`. A file that is missing there is taken from
`Config/Robots/Default/<model>/` or `Config/Robots/Default/`.

| File | Content | How it is made |
|---|---|---|
| `cameraCalibration.cfg` | rotation of the camera and of the body | calibration run on the robot (below) |
| `imuCalibration.cfg` | rotation of the inertial sensor | by hand (see below) |
| `jointCalibration.cfg` | zero offset per joint | by hand |
| `network.cfg` | name and addresses | `bh robot add` |

```bash
./bh calib status
```

shows for every robot which of these files exist, whether robot and repository agree, and whether a
calibration was saved on the robot that is not in the repository yet.

**A calibration is saved on the robot, and a deploy replaces the robot's configuration.** `bh deploy` and
`bh match` therefore copy a newer calibration from the robot into the repository first (the replaced local
file goes to `Config/Robots/<name>/.calibration-backup/`). `bh calib pull NAME` does only that. Commit the
pulled files - they are the calibration of that robot for everybody. `bh deploy --overwrite-calibration`
deliberately sends the repository's version instead.

## Camera calibration

Needed for every new robot, after a fall that bent the head, and after any work on the head.

1. Put the robot on the field so that it sees the lines of a goal area, deploy, and let it stand
   (hold STAND for 2 s, then WALK for 2 s).
2. Attach mission control and start the calibration script in the SimRobot console:

   ```bash
   ./bh connect Alice
   ```

   ```
   call Includes/ManualCameraCalibration
   ```

   The robot switches to calibration mode (back LED orange) and says "Waiting".
3. In the camera image mark field lines: **Ctrl+click** the first point of a line, **Shift+click** the second,
   then tell the robot which line it is with **Ctrl+Shift+I / J / K / L** (the script prints which key means
   which line of the goal area).
4. **Ctrl+Shift+V** starts sampling ("Recording started" ... "Recording finished"). The robot then turns its
   head to the next of three head positions (straight, right, left) and says "Waiting" again - repeat
   steps 3 and 4 for the new view.
5. After the last position it says "Optimizing." and "Calibration finished." and writes
   `Config/Robots/<name>/cameraCalibration.cfg` on the robot.
6. Fetch and commit it:

   ```bash
   ./bh calib pull Alice
   ```

Check the result in the image view: the projected field lines (`vid all representation:FieldLines:image`,
already switched on by `bh connect`) must lie on the real lines, also with the head turned.

`Includes/AutomaticCameraCalibration` also switches to calibration mode, but the behaviour in this release
always runs the manual procedure above. The upstream documentation
(<https://docs.b-human.de/coderelease2026/>) describes the procedure with pictures.

## Inertial sensor and joints

- `imuCalibration.cfg`: the calibration run does not change it in this release, and the automatic
  calibration is switched off (`isAutoCalibrationActive = false` in `imuCalibrationProvider.cfg`). The default
  is "no correction". A robot that leans visibly while standing on flat ground needs either a corrected
  `rotation` in its own `imuCalibration.cfg` or joint offsets.
- `jointCalibration.cfg`: one offset per joint, default 0. Copy the default file to
  `Config/Robots/<name>/` and enter the offsets if a robot's joints are known to be off (for example after a
  repair).

## Closing the sim-to-real gap for the walk

Work from cheap to expensive:

1. **Rule out the robot.** `bh status` (battery above 40 %, no hot joints), `bh robot scan NAME` (no foreign
   software stealing CPU), firmware 1.7.1, camera calibrated, robot stands straight.
2. **Compare the same policy in both worlds.** `bh sim Robot` and the real robot, same commands. The robot
   records a log while it runs; fetch it with `bh logs NAME --download` (the robot goes limp during the
   transfer), replay it with `bh sim Replay` and look at requested versus measured joint angles in
   SimRobot's joint view. `bh connect NAME` shows the same live. A joint that lags or overshoots on the
   robot but not in the simulation points to the motor model.
3. **Adjust the model, not the policy**, where the robot differs:

   | File (`Config/Robots/Default/<model>/` or `Config/Robots/<name>/`) | What it describes |
   |---|---|
   | `motorParameters.cfg` | position gain, damping and torque limit per joint, per stiffness mode |
   | `massCalibration.cfg` | mass, centre of mass and inertia per limb |
   | `robotDimensions.cfg` | lengths of the limbs |
   | `jointLimits.cfg` | allowed joint ranges |
   | `walkingEngine.cfg` | how the walk policy is commanded: speeds, scaling, which networks |

   The simulated robots have their own `motorParameters.cfg` and `walkingEngine.cfg` in
   `Config/Robots/SimulatedK1` / `SimulatedT1`. If a real robot only walks well with different gains than the
   simulated one, the training environment has to learn about that difference too.
4. **Retrain with what you measured** (domain randomisation around the measured values) and bring the policy
   in with `bh policy use ... --scope sim`, then `--scope robot` ([policies.md](policies.md)).
5. **Test in this order:** simulator → robot held or on a soft surface under the laptop guard → free on the
   field ([testing-and-safety.md](testing-and-safety.md)).

Per-robot differences belong in `Config/Robots/<name>/`, differences of the robot type in
`Config/Robots/Default/<model>/`. A parameter changed for one robot in the type's file changes all robots.
