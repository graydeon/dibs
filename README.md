![dibs: heavy jobs take a number, short ones go first](assets/banner.png)

# dibs: a turn-based queue for heavy jobs on one machine

AI agents and scripts happily start five `make -j16` at the same time and freeze the desktop. `dibs run` makes heavy commands (builds, test suites, video exports) wait for a license and run inside a capped systemd scope, so they take turns instead of all starting at once. Short jobs go first, and the machine stays usable.

If you only need a queue for shell commands, pueue and task-spooler are mature and worth a look. dibs is for one machine shared by people and agents: it caps each job's CPU and memory with cgroups, runs short jobs first, freezes a long job so a short one can get through, and its example hook keeps an agent from blocking on a foreground job.

A real run, with the CPU cap set to 2 and `freeze_after` to 5s: a long job holds both CPUs, a short job arrives, the long one is frozen, the short one runs, the long one is thawed. Both terminals' status lines are merged here in time order.

```
[dibs] ticket #1 (long lane), 0 ahead
[dibs] licensed after 0m00s: 2 cpus, 512M mem
[dibs] ticket #2 (short lane), 0 ahead
[dibs] frozen: short job #2 needs CPU
[dibs] licensed after 0m08s: 2 cpus, 512M mem
short-done
[dibs] exit 0 after 0m08s total
[dibs] thawed after 0m02s frozen in total
```

Measured on the author's desktop: 338 jobs in its first three days, 32 freezes, longest wait 17m35s.

## Requirements

- Linux with a systemd user session, cgroup v2, and the `cpu` and `memory` controllers delegated to the user manager. Debian 13 and recent Ubuntu do this by default. Check with `cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers`.
- The user D-Bus session bus, which dibs needs to freeze and stop scopes. Desktop installs have it; on a minimal or server install add the `dbus-user-session` package (Debian and Ubuntu) and log in again. Without it, the daemon refuses to start and says so.
- Python 3.11 or newer. No other dependencies.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/graydeon/dibs/main/install.sh | bash
```

Or read it first: `curl -fsSLO https://raw.githubusercontent.com/graydeon/dibs/main/install.sh`, look at it, then `bash install.sh`.
From a clone: `git clone https://github.com/graydeon/dibs && cd dibs && ./install.sh`.

Manual install:

```bash
install -Dm755 dibs ~/.local/bin/dibs        # ~/.local/bin must be on PATH
install -Dm644 dibsd.service ~/.config/systemd/user/dibsd.service
systemctl --user daemon-reload
systemctl --user enable --now dibsd
dibs run --est 1m --cpus 1 --mem 512M -- echo ok
```

On a machine without a desktop login, run `loginctl enable-linger` once so the user session (and the daemon) keeps running.

## Usage

```bash
dibs run --est 20m --cpus 8 --mem 16G -- make -j8
dibs run --long --cpus 8 --mem 8G -- 'ffmpeg -i talk.mov talk.mp4 && ffmpeg -i demo.mov demo.mp4'
dibs ls                  # queued and running jobs
dibs run --help          # shows this machine's CPU cap
```

- `--est` is the expected runtime (`90s`, `10m`, `2h`). `--cpus` and `--mem` are what the job reserves and is capped to. Defaults: `--est 10m --cpus 4 --mem 4G` (`--cpus` defaults to the cap if that is below 4).
- `--long` puts the job in the long lane whatever its estimate. `--label` names it in `dibs ls`.
- A single quoted argument runs through `bash -c`.
- Do not queue long-running servers. Run those as their own units.

## How results come back

`dibs run` holds a ticket open with the daemon, waits for its license, then runs the command itself inside `dibs-<id>.scope` with `CPUQuota` set to the CPUs requested, `MemoryHigh` to the memory requested and `MemoryMax` to 1.5 times that. Stdout, stderr and the exit code are the command's own, so whatever called `dibs run` gets the result directly. dibs's own status lines go to stderr with the prefix `[dibs]`.

When the command exits, anything it left running in its scope is stopped. If the client dies (Ctrl-C, killed, terminal closed), the daemon stops its scope, thawing it first if frozen, and frees the license.

## Policy

The timings below are the defaults; see Config.

- Capacity is counted from what jobs request, not measured load. Running jobs together may request up to the CPU cap. A job is also only granted if `MemAvailable` minus its request stays at or above the memory reserve.
- Short lane: `--est` up to 15m. Short jobs go first. A long job that has waited 30m counts as short.
- A job that runs past 1.5 times its estimate plus 2m moves to the long lane, and the client prints a note.
- Freezing: if a short job has waited 20s for CPU, the newest running long job is frozen with the cgroup freezer. Its memory stays allocated. It is thawed when no short job is waiting. A job is never frozen for more than 60m in total.
- Backfill: smaller jobs further down the queue may start ahead of a bigger head job, until the head has waited 10m.

Every event is logged as one JSON line to `~/.local/state/dibs/jobs.jsonl`.

## Config

None is needed. The CPU cap defaults to the thread count minus 2 (at least 1), and the memory reserve to 10% of RAM. To change those or the lane timings above, copy `config.example.toml` to `~/.config/dibs/config.toml`, edit it, and run `systemctl --user restart dibsd`. An unknown key or bad value stops dibs with a message naming the key.

## Agents

An agent calling `dibs run` in the foreground blocks until the job ends and then gets its output and exit code, within whatever tool timeout it has.

For Claude Code, `claude-hook.py` is an example PreToolUse hook. It denies any `dibs run` that is not a background Bash call and tells the agent to re-issue it with `run_in_background: true`. Claude Code re-invokes the agent when a background command exits, so the job's full output arrives as its next message, with no polling and no tool timeout. Add it to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash", "hooks": [{ "type": "command", "command": "/path/to/claude-hook.py" }] }
    ]
  }
}
```

## Limits

- It only governs jobs started through `dibs run`. Other processes are invisible to it, apart from the live memory check.
- One machine. It is not a cluster scheduler.
- A job that asks for more memory than the machine can spare above the reserve waits until that much is free.

## Tests

`python3 -m unittest test_dibs` checks the scheduler decisions without a daemon or systemd.

Also by me: [homenet](https://github.com/graydeon/homenet) · [sbx](https://github.com/graydeon/sbx) · [Mousa](https://github.com/graydeon/mousa)

## License

MIT
