# Connecting to the GameController

The GameController is the referee's computer. It broadcasts the game state (UDP port 3838) to all robots,
and the robots answer it (UDP port 3939). Nothing has to be configured on the robot except the **team
number**: a robot only obeys packets of a game in which its team plays.

## Is it working?

```bash
./bh gc doctor
```

Run it on a computer in the same network as the robots. It tells you

- whether GameController packets arrive at all, from which address, and what the game state is;
- whether **more than one** GameController is sending (robots would flip between them);
- whether the running game contains this code's team number (`teamNumber` in `Config/settings.cfg`);
- for every reachable robot: the team and player number it was deployed with, duplicate player numbers,
  and player numbers beyond the number of players in the game.

On the robot itself, in match LED mode: **white blinking = no GameController heard**, white steady = heard
([leds.md](leds.md)).

```bash
./bh gc listen
```

prints the game state whenever it changes (state, half, time, score, set plays, penalties of the own team).

## The usual reasons a robot ignores the GameController

| Symptom | Cause | Fix |
|---|---|---|
| doctor: "no GameController heard" | not in the same network, or a firewall drops UDP 3838 | join the field network; on Windows allow Python for private networks |
| doctor: "the running game is for teams X and Y" | wrong team selected in the GameController, or wrong `teamNumber` | select the right team, or `bh deploy ... --team N` |
| doctor: robot "is deployed with team number N" | the robot was deployed for another team number | deploy again |
| two robots with the same player number | deployed without `name:number` | `bh deploy Alice:1 Bob:2` |
| robot reacts late or not at all, doctor is fine on the laptop | the robot is in another network than the laptop (cable vs. wireless) | check the profile: `bh deploy ... --profile NAME`, then `bh status` |
| robot does not react to anything | it is limp (blue blinking) or in calibration mode (orange) - it ignores the GameController then | hold F1 for 2 s ("Body ready"), then STAND for 2 s, then WALK for 2 s |

Without a GameController the robot follows its buttons. As soon as it hears one, the GameController is in
charge; when the packets stop, the robot falls back to its buttons after 5 seconds.

## Practising without the official GameController

```bash
./bh gc test
```

runs a minimal GameController for the own team. It refuses to start if another GameController is already
sending (`--force` overrides; never do that at a competition).

```
i / r / s / p / f     initial / ready / set / playing / finished
stop  /  go           freeze all robots where they stand / release them
pen <n> / unpen <n>   penalize / unpenalize player <n>
goal us | goal them   count a goal and go to ready
k                     switch the kick-off team
half                  switch half
?                     what each robot reports: position, ball, fallen or not
q                     quit
```

`bh gc test Alice Bob` addresses these robots directly in addition to the broadcast, `--players N` sets the
team size, `--opponent N` the team number of the pretend opponent. The same controller is available as
buttons with `bh dash --gc` ([dashboard.md](dashboard.md)).

It is for practice: it has no rule enforcement, no timers for set plays and no opponent. For a real test
game use the official GameController of the league (the upstream documentation explains where to get it);
`bh gc doctor` and `bh gc listen` work with both.

## Protocol version

This code speaks GameController protocol version 20 (`Util/GameController/include/RoboCupGameControlData.h`).
`bh gc doctor` says so explicitly when it receives packets of another version; the robots silently ignore
those.
