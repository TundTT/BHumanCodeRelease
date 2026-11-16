# Agents

The guide for coding agents working in this repository is [CLAUDE.md](CLAUDE.md). It applies to every
agent, whatever tool it runs in: commands, what can be verified on which machine, the map of the code, the
conventions, the performance-critical areas and the known pitfalls.

Short version:

1. Match performance first, safety second, convenience third.
2. `./bh test` must pass after every change to `Util/bh`, `Make/Common` or `Install`.
3. C++ that was not compiled, and motion or behaviour that was not run, is reported as such - never as done.
4. User guides are in [Docs/](Docs/README.md); keep them, the help texts and the code in agreement.
