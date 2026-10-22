# Trading Lab

A personal workspace for learning how trading strategies are designed, tested and evaluated.

**Paper trading only. Educational use only.** Nothing here places real orders or uses real money, and nothing in this repo is financial advice.

## Layout

| Folder | Purpose |
| --- | --- |
| `data/` | Market data used for research (keep large/raw files out of Git) |
| `strategies/` | Strategy definitions and ideas |
| `backtests/` | Backtest runs and results |
| `notebooks/` | Exploratory Jupyter notebooks |

## Getting started

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv run check_lab.py
```

This prints the Python version and today's date to confirm the environment works.
Hooks in .claude/hooks guard the paper-only rule.

## Running it every day (paper trading)

`scheduler/` runs one trading day by itself, in New York time, using the NYSE calendar (holidays skipped, half days end early, daylight saving handled):

| When (New York) | What |
| --- | --- |
| 09:00 | prep: update the bar cache (`data_pipeline`), then the gap scanner |
| 09:25 | start the live runner (`scheduler/schedule.yaml` says which strategy and symbol) |
| close - 5 min (15:55; 12:55 on a half day) | the runner sells anything it holds |
| close + 5 min (16:05; 13:05) | the runner is stopped (like Ctrl+C) and `data/live/reports/YYYY-MM-DD.md` is written |

The scheduler never clears the STOP file (`data/live/STOP`, written by `uv run -m live.kill`): while it exists the runner is not started, and the report says so. A halted runner is not resumed either. Remove STOP yourself with `uv run -m live.kill --clear` when you are ready.

```bash
uv run -m scheduler --next 5                 # the next 5 trading days and their times
uv run -m scheduler --today --dry-run        # today's plan, runs nothing
uv run -m scheduler --today                  # run today's session, then exit (exits at once on a holiday)
nohup uv run -m scheduler >> data/live/scheduler.log 2>&1 &    # daemon: every trading day, until you stop it
```

The IB Gateway (paper, port 4002) must be running and logged in; the scheduler does not start it.

### Letting your operating system start it

The program decides holidays and half days itself, so the operating system only has to run `uv run -m scheduler --today` on weekdays shortly before 09:00 New York time (08:55 leaves a margin). Use the same command everywhere. The system clock should be set to New York time, or the trigger converted for your zone (the program's own times are always New York).

**Linux (systemd user timer)** (this lab's own machine is a container with no systemd or cron, so it uses the daemon above):

```ini
# ~/.config/systemd/user/trading-lab.service
[Service]
WorkingDirectory=/path/to/trading-lab
ExecStart=/path/to/uv run -m scheduler --today

# ~/.config/systemd/user/trading-lab.timer
[Timer]
OnCalendar=Mon..Fri 08:55 America/New_York
Persistent=false
[Install]
WantedBy=timers.target
```
Then `systemctl --user enable --now trading-lab.timer` and check with `systemctl --user list-timers`. (cron works too: `CRON_TZ=America/New_York` then `55 8 * * 1-5 cd /path/to/trading-lab && uv run -m scheduler --today`, where your cron supports `CRON_TZ`.)

**macOS (launchd)**: `~/Library/LaunchAgents/lab.trading.scheduler.plist` with `ProgramArguments` = `/path/to/uv`, `run`, `-m`, `scheduler`, `--today`, `WorkingDirectory` = the repo, and one `StartCalendarInterval` dict per weekday (`Weekday` 1-5, `Hour` 8, `Minute` 55; it uses the Mac's local time, so set the Mac to New York time). Load it with `launchctl load ~/Library/LaunchAgents/lab.trading.scheduler.plist`.

**Windows (Task Scheduler)**: Create Task, trigger "Weekly" Monday to Friday at 08:55 (local time, so set Windows to the Eastern time zone), action "Start a program": program `uv`, arguments `run -m scheduler --today`, "Start in" the repo folder. Tick "Run whether user is logged on or not" only if the IB Gateway runs in the same session. On Windows the scheduler stops the runner with a plain terminate instead of Ctrl+C; its state file is written after every change, so the next start reconciles it. Running everything inside WSL avoids the difference.
