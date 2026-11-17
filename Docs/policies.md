# Walk, kick and get-up policies

The robot's walking, kicking and getting up are neural-network policies: ONNX files in
`Config/NeuralNets/Walk` and `Config/NeuralNets/GetUpEngine`. Swapping one is a configuration change, not a
code change - as long as the new policy has the same inputs and outputs.

## The five slots

| Slot | Used for | Configuration file | Attribute | K1 in → out | T1 in → out |
|---|---|---|---|---|---|
| `walk` | walking without the ball | `walkingEngine.cfg` | `walkNeuronalNetworkParameters.modelName` | 344 → 13 | 365 → 14 |
| `kick` | dribbling and kicking | `walkingEngine.cfg` | `ballNetworkParameters.kickModel` | 59 → 13 | 62 → 14 |
| `kick-soft` | soft, precise kicks | `walkingEngine.cfg` | `oldBallNetworkParameters.kickModel` | 59 → 13 | 62 → 14 |
| `getup` | getting up | `getUpEngine.cfg` | `policyName` | 73 → 22 | 76 → 23 |
| `getup-fast` | fast get-up | `getUpEngine.cfg` | `policyNameFast` | 72 → 22 | 75 → 23 |

The sizes are those of the shipped policies. Each configuration file exists several times (real robot,
simulated robot, some scenarios); `bh policy list` shows every copy and whether its policy fits.

## Why there is a check

The robot verifies that a policy matches the observation vector only with assertions, and assertions are
compiled out of **Release** builds. A policy with a different input size would then read or write beyond
its buffers on the robot during a game. Therefore `bh deploy`, `bh match` and `bh policy push` refuse to
send anything while `bh policy check` fails.

## Trying a new policy

```bash
./bh policy info my_walk.onnx
```

```bash
./bh policy use walk my_walk.onnx --model k1 --scope sim
```

```bash
./bh sim
```

```bash
./bh policy use walk my_walk.onnx --model k1
```

```bash
./bh policy push Alice
```

1. `info` prints the input and output shape of any ONNX file.
2. `use ... --scope sim` validates the file against every configuration it would change, copies it into
   `Config/NeuralNets`, and switches **only the simulated robot** to it. Nothing is changed if the sizes do
   not fit. An existing file of the same name is never overwritten silently (`--overwrite`); give new
   policies new names, so that old results stay reproducible.
3. Try it in the simulator; for numbers use `bh eval` ([simulation.md](simulation.md)).
4. `use` without `--scope` switches the real robots' configuration too (`--scope robot`: only them).
5. `push` sends just the policy files and their configuration to a robot and restarts the robot software -
   no build, no full deploy. It shows what differs and asks first, because the robot goes limp for a moment
   (`--dry-run`, `-y`). The first real-robot test belongs under the laptop guard
   ([testing-and-safety.md](testing-and-safety.md)).

Going back is the same command with the old file name, or `git checkout` of the changed `.cfg` files.

The same steps without typing: the **Policies** tab of `bh dash` - drop the file, drag it onto a slot, try it
in the simulator, send it to a robot ([dashboard.md](dashboard.md)).

## When the sizes differ

A policy trained with a different observation or action layout cannot be dropped in. The observation vector
is assembled in C++:

- walking and kicking: `Src/Modules/MotionControl/WalkingEngine/` (the sizes are checked in
  `WalkingEngine::compile`),
- getting up: `Src/Modules/MotionControl/GetUpEngine/`.

Change the code that fills the observation, then `numInput` / `numOutput` (and the `...Fast` variants) in
every copy of the configuration file, then run `bh policy check`. Order, scaling and history length of the
observations must match training exactly; the size check cannot verify that. Compare a few observation
vectors from the simulator with your training environment before trusting a new layout.

## What a policy is held to

[motion-requirements.md](motion-requirements.md) lists the speeds, kick strengths and get-up times the
behaviour assumes and the simulator measured; `bh policy limits` prints the configuration side of it.

## Sim-to-real notes for policies

- A policy that only works in the simulator usually meets a robot whose joints, mass or delays differ from
  the model. See [calibration.md](calibration.md) before retraining.
- Test order: simulator → robot held or on a soft surface under the laptop guard → free on the field.
- Keep the previous policy file in the repository until the new one has played a full test game.
