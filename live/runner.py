"""The runner: one strategy on one symbol, polled once a minute. Paper trading only.

Each poll: sync the position with IB (a stop or target may have filled), fetch the closed bars,
step every bar newer than the state file's `last_bar_ts` in order, and act on the decisions.
Then the wall-clock rule: flatten before the close and never carry a position overnight.

What differs from the backtester, on purpose (the bars are 15-20 minutes late):
- stop and target rest at IB as a pair instead of being checked bar by bar;
- a wall-clock deadline flattens before the close (the strategies' own time exit would arrive after it);
- an entry signal older than `max_signal_age_min` is skipped (exits are never skipped).
`wall_clock_rules=False` turns the last two off; the parity test uses it.
"""

import json
import logging
import math
from pathlib import Path

import pandas as pd
from ib_async import Stock

from strategies.base import Entry, Exit
from strategies.session_clock import SessionClock

from .broker import Broker
from .config import (FLATTEN_BEFORE_CLOSE_MIN, LOOKBACK_BARS, MAX_SIGNAL_AGE_MIN, POLL_SECONDS,
                     STATE_DIR)
from .execution import ExecutionHalt, Executor, utc_now
from .feed import BAR, NY, BarFeed
from .reconcile import ib_shares, reconcile
from .registry import build
from .state import LiveState, PositionState, load, lock, run_dir, save, settings_key
from .step import step, window

log = logging.getLogger("live")


class Runner:
    def __init__(self, ib, name: str, symbol: str, settings: dict, *, strategy=None, dry_run: bool = False,
                 root: Path = STATE_DIR, clock=utc_now, feed: BarFeed | None = None, executor: Executor | None = None,
                 wall_clock_rules: bool = True, catch_up: int = 0, flatten_on_mismatch: bool = False,
                 max_signal_age_min: int = MAX_SIGNAL_AGE_MIN, flatten_before_close_min: int = FLATTEN_BEFORE_CLOSE_MIN,
                 lookback: int = LOOKBACK_BARS, contract=None):
        self.ib, self.name, self.symbol, self.dry_run, self.clock = ib, name, symbol, dry_run, clock
        self.contract = contract or Stock(symbol, "SMART", "USD")
        self.tag = f"live:{name}:{symbol}"
        self.broker = Broker(ib, self.tag, dry_run=dry_run)
        self.dir = run_dir(name, symbol, root)
        self.state_path = self.dir / ("state_dryrun.json" if dry_run else "state.json")
        self.key = settings_key(name, symbol, settings)
        self.strategy = strategy or build(name, settings)
        self.feed = feed or BarFeed(ib, self.contract, delay_log=self.dir / "bar_delay.csv", clock=clock)
        self.executor = executor or Executor(ib, self.broker, self.contract, clock=clock)
        self.wall_clock_rules, self.catch_up = wall_clock_rules, catch_up
        self.flatten_on_mismatch = flatten_on_mismatch
        self.max_signal_age = pd.Timedelta(minutes=max_signal_age_min)
        self.flatten_before_close = pd.Timedelta(minutes=flatten_before_close_min)
        self.lookback = lookback
        self.sessions = SessionClock()
        self.state: LiveState | None = None

    # -- start ---------------------------------------------------------------------------

    def start(self) -> None:
        """Load the state, compare it with IB, and refuse to trade if anything is unexplained."""
        self.ib.qualifyContracts(self.contract)
        self.state = load(self.state_path, self.name, self.symbol, self.key)
        if hasattr(self.strategy, "reset"):
            self.strategy.reset()  # the backtester does this too
        res = reconcile(self.ib, self.broker, self.state)
        if res.problems and self.flatten_on_mismatch:
            res = self._flatten_mismatch(res)
        if res.problems:
            self._halt("reconcile: " + "; ".join(res.problems))
            return
        if self.state.halted:
            log.warning("start: previous halt (%s) cleared, IB and the state file agree", self.state.halted)
            self.state.halted = None
        if res.needs_protection:
            self.executor.protect(self.state.position)
        self._save()

    def _flatten_mismatch(self, res):
        """The one automatic fix, only on request: sell shares IB holds that the state file cannot explain."""
        held = ib_shares(self.ib, self.symbol)
        if held <= 0 or any("not ours" in p for p in res.problems):
            return res
        log.warning("start: --flatten-on-mismatch, selling %d %s", held, self.symbol)
        closed = self.feed.poll()
        if closed.empty:
            return res
        stray = PositionState(shares=held, entry_time=self.clock().isoformat(), entry_price=0.0, stop=0.0, target=None)
        self.executor.exit(stray, float(closed["close"].iloc[-1]))
        self.state.position = None
        return reconcile(self.ib, self.broker, self.state)

    def _halt(self, reason: str) -> None:
        log.error("HALT: %s", reason)
        self.state.halted = reason
        self._save()

    def _save(self) -> None:
        save(self.state_path, self.state)

    def _append(self, filename: str, record: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / filename).open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    # -- one poll ------------------------------------------------------------------------

    def tick(self) -> None:
        if self.state.halted:
            log.error("halted, not trading: %s", self.state.halted)
            return
        try:
            self._tick(self.clock())
        except ExecutionHalt as e:
            self._halt(str(e))

    def _tick(self, now: pd.Timestamp) -> None:
        closed = self.feed.poll()
        if closed.empty:
            return
        self._sync_position(now)
        if self.state.halted:
            return
        if self.state.last_bar_ts is None:  # first run: start from now, never trade on history
            back = self.catch_up if self.dry_run else 0
            anchor = closed.index[max(0, len(closed) - 1 - back)]
            self.state.last_bar_ts = anchor.isoformat()
            self._save()
            log.info("first run: starting after bar %s", anchor)
        last = pd.Timestamp(self.state.last_bar_ts)
        for ts in closed.index[closed.index > last]:
            self._process_bar(closed, ts, now)
            if self.state.halted:
                return
        self._wall_clock_exit(closed, now)

    def _sync_position(self, now: pd.Timestamp) -> None:
        """Has IB closed our position (stop or target) since the last poll? Remember when, for the bar stepping."""
        pos = self.state.position
        if pos is None or pos.closed_at or self.dry_run:
            return
        shares = ib_shares(self.ib, self.symbol)
        if shares == pos.shares:
            return
        if shares != 0:
            self._halt(f"State says {pos.shares} {self.symbol}, IB holds {shares}")
            return
        pos.closed_at, pos.exit_price, pos.exit_reason = self._closing_info(pos, now)
        log.info("sync: IB closed the position (%s) at %s", pos.exit_reason, pos.closed_at)
        self._save()

    def _closing_info(self, pos: PositionState, now: pd.Timestamp) -> tuple[str, float | None, str]:
        for trade in self.ib.trades():
            oid = trade.order.orderId
            if oid in (pos.stop_order_id, pos.target_order_id) and trade.orderStatus.status == "Filled":
                when = trade.fills[-1].execution.time if trade.fills else None
                when = now if when is None else pd.Timestamp(when)
                when = when.tz_localize("UTC") if when.tzinfo is None else when.tz_convert("UTC")
                return when.isoformat(), float(trade.orderStatus.avgFillPrice), "stop" if oid == pos.stop_order_id else "target"
        return now.isoformat(), None, "closed_while_unwatched"

    def _process_bar(self, closed: pd.DataFrame, ts: pd.Timestamp, now: pd.Timestamp) -> None:
        st, bar_close = self.state, ts + BAR
        pos = st.position
        if pos is not None and pos.closed_at and pd.Timestamp(pos.closed_at) < bar_close:
            self._record_trade(pos, exit_time=pd.Timestamp(pos.closed_at), exit_price=pos.exit_price,
                               reason=pos.exit_reason)
            st.position = None
        bars = window(closed, ts, self.lookback)
        intent = step(self.strategy, bars, st.position)
        if intent is not None:
            self._act(intent, ts, bars, now)
        if not st.halted:
            st.last_bar_ts = ts.isoformat()
            self._save()

    def _act(self, intent: Entry | Exit, ts: pd.Timestamp, bars: pd.DataFrame, now: pd.Timestamp) -> None:
        st, bar_close = self.state, ts + BAR
        ref = float(bars["close"].iloc[-1])
        record = {"bar": ts.isoformat(), "kind": type(intent).__name__.lower(), "reason": intent.reason,
                  "seen_at": now.isoformat(), "age_s": int((now - bar_close).total_seconds())}
        if isinstance(intent, Entry):
            record.update(stop=intent.stop, target=None if math.isinf(intent.target) else intent.target)
            blocked = self._entry_blocked(now, now - bar_close)
            shares = 0 if blocked else self.executor.size(intent, ref)
            record["shares"] = shares
            if blocked or shares == 0:
                record["action"] = f"skipped: {blocked or 'size 0 (stop not below the price)'}"
                self._append("signals.jsonl", record)
                log.info("signal %s entry skipped: %s", ts, record["action"])
                return
            fill = self.executor.buy(shares, ref)
            record["action"] = "bought" if fill else "not filled" if not self.dry_run else "dry run"
            self._append("signals.jsonl", record)
            if fill is None:
                return
            st.position = PositionState(shares=fill.shares, entry_time=bar_close.isoformat(), entry_price=fill.price,
                                        stop=intent.stop, target=record["target"])
            self._save()  # the position exists at IB now: write it down before anything else can fail
            self.executor.protect(st.position)
            self._save()
            return
        # Exit: never skipped because it is old.
        pos = st.position
        fill = self.executor.exit(pos, ref)
        record["action"] = "sold" if fill else "dry run" if self.dry_run else "nothing left to sell"
        self._append("signals.jsonl", record)
        if self.dry_run:
            return
        if fill is not None:
            self._record_trade(pos, exit_time=bar_close, exit_price=fill.price, reason=intent.reason, fill_time=fill.time)
        else:  # the stop or target got there first
            when, price, why = self._closing_info(pos, now)
            self._record_trade(pos, exit_time=pd.Timestamp(when), exit_price=price, reason=why)
        st.position = None

    def _entry_blocked(self, now: pd.Timestamp, age: pd.Timedelta) -> str:
        if age > self.max_signal_age:
            return f"signal is {age} old"
        if self.feed.holes:
            return f"{len(self.feed.holes)} bar(s) missing in the window"
        if self.wall_clock_rules:
            deadline = self._deadline(now)
            if deadline is None or now >= deadline:
                return "market closed or past the flatten deadline"
        return ""

    def _deadline(self, now: pd.Timestamp) -> pd.Timestamp | None:
        close = self.sessions.close(now.tz_convert(NY).date())
        return None if close is None else close - self.flatten_before_close

    def _wall_clock_exit(self, closed: pd.DataFrame, now: pd.Timestamp) -> None:
        pos = self.state.position
        if not self.wall_clock_rules or pos is None or pos.closed_at or self.dry_run:
            return
        carried = pd.Timestamp(pos.entry_time).tz_convert(NY).date() != now.tz_convert(NY).date()
        deadline = self._deadline(now)
        if not carried and (deadline is None or now < deadline):
            return
        why = "overnight" if carried else "flatten_deadline"
        log.warning("wall clock: closing %d %s (%s)", pos.shares, self.symbol, why)
        fill = self.executor.exit(pos, float(closed["close"].iloc[-1]))
        if fill is not None:
            self._record_trade(pos, exit_time=now, exit_price=fill.price, reason=why, fill_time=fill.time)
        else:
            when, price, reason = self._closing_info(pos, now)
            self._record_trade(pos, exit_time=pd.Timestamp(when), exit_price=price, reason=reason)
        self.state.position = None
        self._save()

    def _record_trade(self, pos: PositionState, *, exit_time, exit_price, reason, fill_time=None) -> None:
        self._append("trades.jsonl", {
            "entry_time": pos.entry_time, "entry_price": pos.entry_price, "shares": pos.shares,
            "stop": pos.stop, "target": pos.target, "exit_time": exit_time, "exit_price": exit_price,
            "reason": reason, "exit_fill_time": fill_time})
        log.info("trade closed: %s", reason)

    # -- loop ----------------------------------------------------------------------------

    def run(self, once: bool = False) -> None:
        with lock(self.dir):
            self.start()
            while True:
                self.tick()
                if once or self.state.halted:
                    break
                self.ib.sleep(POLL_SECONDS)
