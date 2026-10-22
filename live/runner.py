"""The runner: one strategy on one symbol, polled once a minute. Paper trading only.

Each poll: sync the position with IB (a stop or target may have filled), fetch the closed bars,
step every bar newer than the state file's `last_bar_ts` in order, and act on the decisions.
Then the wall-clock rule: flatten before the close and never carry a position overnight.

What differs from the backtester, on purpose (the bars are 15-20 minutes late):
- stop and target rest at IB as a pair instead of being checked bar by bar;
- a wall-clock deadline flattens before the close (the strategies' own time exit would arrive after it);
- an entry signal older than `max_signal_age_min` is skipped (exits are never skipped);
- a halt (stuck exit, unexplained position, too many failed polls) stays until you restart with `clear_halt`.
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

from .broker import Broker, PaperGuardError, check_paper
from .config import (CLIENT_ID, FLATTEN_BEFORE_CLOSE_MIN, HOLE_WAIT_MIN, HOST, LOOKBACK_BARS, MAX_SIGNAL_AGE_MIN,
                     MAX_TICK_ERRORS, POLL_SECONDS, PORT, RECONNECT_ATTEMPTS, RECONNECT_WAIT_S, STATE_DIR)
from .console import bar_line, position_text, say
from .execution import DuplicateEntry, ExecutionHalt, Executor, utc_now
from .feed import BAR, NY, BarFeed
from .reconcile import ib_shares, reconcile
from .refs import entry_ref, owns
from .risk import Risk, RiskRefused, stop_path
from .registry import build
from .state import LiveState, PositionState, StopFileError, load, lock, run_dir, save, settings_key
from .step import step, window

log = logging.getLogger("live")


class Runner:
    def __init__(self, ib, name: str, symbol: str, settings: dict, *, strategy=None, dry_run: bool = False,
                 root: Path = STATE_DIR, clock=utc_now, feed: BarFeed | None = None, executor: Executor | None = None,
                 wall_clock_rules: bool = True, catch_up: int = 0, flatten_on_mismatch: bool = False,
                 clear_halt: bool = False, hole_wait_min: int = HOLE_WAIT_MIN,
                 max_signal_age_min: int = MAX_SIGNAL_AGE_MIN, flatten_before_close_min: int = FLATTEN_BEFORE_CLOSE_MIN,
                 lookback: int = LOOKBACK_BARS, contract=None, risk: Risk | None = None, reconnect=None,
                 reconnect_attempts: int = RECONNECT_ATTEMPTS, reconnect_wait_s: float = RECONNECT_WAIT_S, port: int = PORT):
        self.ib, self.name, self.symbol, self.dry_run, self.clock = ib, name, symbol, dry_run, clock
        self.contract = contract or Stock(symbol, "SMART", "USD")
        self.tag = f"live:{name}:{symbol}"
        self.broker = Broker(ib, self.tag, dry_run=dry_run, risk=risk, port=port)
        self.dir = run_dir(name, symbol, root)
        self.state_path = self.dir / ("state_dryrun.json" if dry_run else "state.json")
        self.key = settings_key(name, symbol, settings)
        self.strategy = strategy or build(name, settings)
        self.feed = feed or BarFeed(ib, self.contract, delay_log=self.dir / "bar_delay.csv", clock=clock)
        self.executor = executor or Executor(ib, self.broker, self.contract, clock=clock)
        self.wall_clock_rules, self.catch_up = wall_clock_rules, catch_up
        self.flatten_on_mismatch, self.clear_halt = flatten_on_mismatch, clear_halt
        self.hole_wait = pd.Timedelta(minutes=hole_wait_min)
        self._last_ref: float | None = None  # newest close seen: the price an exit is built from if a poll fails
        self._errors = 0
        self.max_signal_age = pd.Timedelta(minutes=max_signal_age_min)
        self.flatten_before_close = pd.Timedelta(minutes=flatten_before_close_min)
        self.lookback = lookback
        self.sessions = SessionClock()
        self.state: LiveState | None = None
        self.stop_file = stop_path(root)
        self.reconnect, self.reconnect_attempts, self.reconnect_wait_s, self.port = reconnect, reconnect_attempts, reconnect_wait_s, port
        self.stopped = False  # the STOP file was seen: nothing more is done until it is removed

    # -- start ---------------------------------------------------------------------------

    def start(self) -> None:
        """Load the state, compare it with IB, and refuse to trade if anything is unexplained."""
        if self.stop_file.exists():
            raise StopFileError(f"{self.stop_file} exists (kill switch): not starting. Remove it to allow trading again.")
        self.ib.qualifyContracts(self.contract)
        accounts = check_paper(self.ib)
        say(f"account   : {', '.join(accounts)}  paper account (every account starts with DU), "
            f"{HOST}:{self.port}, client ID {CLIENT_ID}")
        say(f"run       : {self.name} on {self.symbol}" + ("  [DRY RUN: no orders will be sent]" if self.dry_run else ""))
        existed = self.state_path.exists()
        self.state = load(self.state_path, self.name, self.symbol, self.key)
        say(f"state     : {self.state_path}  "
            + (f"loaded, last bar stepped {self._et(self.state.last_bar_ts)}" if existed and self.state.last_bar_ts
               else "loaded" if existed else "none yet, starting fresh"))
        if hasattr(self.strategy, "reset"):
            self.strategy.reset()  # the backtester does this too
        res = reconcile(self.ib, self.broker, self.state)
        if res.problems and self.flatten_on_mismatch and not self.dry_run:
            res = self._flatten_mismatch(res)
        self._report(res)
        if res.problems:
            self._halt("reconcile: " + "; ".join(res.problems))
            return
        if self.state.halted:
            if not self.clear_halt:
                log.error("start: halted since the last run (%s). Check IB, then restart with --clear-halt.",
                          self.state.halted)
                self._save()
                return
            log.warning("start: previous halt (%s) cleared by --clear-halt", self.state.halted)
            self.state.halted = None
        try:
            if res.needs_protection:
                say(f"            did: no stop/target found at IB for the open position, placing a new one "
                    f"(stop {self.state.position.stop:.2f})")
                self._protect_or_flatten(self.state.position, self._ref_price())
        except ExecutionHalt as e:
            self._halt(str(e))
            return
        self._save()

    def _report(self, res) -> None:
        say(f"reconcile : state says {res.state_text} | IB says {res.ib_text}")
        for line in res.actions:
            say(f"            did: {line}")
        for line in res.problems:
            say(f"            PROBLEM: {line}")
        say("            -> " + ("OK" if res.ok else "NOT OK, halting: nothing will be traded"))

    @staticmethod
    def _et(iso: str | None) -> str:
        return "-" if not iso else f"{pd.Timestamp(iso).tz_convert('America/New_York'):%Y-%m-%d %H:%M} ET"

    def _ref_price(self) -> float:
        if self._last_ref is None:
            closed = self.feed.poll()
            if closed.empty:
                raise ExecutionHalt("no price to build an order from")
            self._last_ref = float(closed["close"].iloc[-1])
        return self._last_ref

    def _flatten_mismatch(self, res):
        """The one automatic fix, only on request: sell shares IB holds beyond what the state file explains.

        The state's own position (and its stop) is left alone; only the excess is sold.
        """
        held = ib_shares(self.ib, self.symbol)
        known = self.state.position.shares if self.state.position else 0
        excess = held - known
        if excess <= 0 or any("not ours" in p for p in res.problems):
            return res
        log.warning("start: --flatten-on-mismatch, selling the %d extra %s", excess, self.symbol)
        stray = PositionState(shares=excess, entry_time=self.clock().isoformat(), entry_price=0.0, stop=0.0, target=None)
        self.executor.exit(stray, self._ref_price())
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

    def _stop_seen(self) -> bool:
        """The STOP file stops all trading, at once and without a halt: removing the file is enough to resume."""
        if not self.stop_file.exists():
            return False
        if not self.stopped:
            self.stopped = True
            log.error("STOP file %s found: no more trading", self.stop_file)
            say(f"STOP      : {self.stop_file} exists (kill switch). Nothing more will be traded; the runner exits. "
                "Remove the file to allow trading again.")
            self._save()
        return True

    def _ensure_connected(self) -> None:
        """The API connection dropped: get it back, then trust nothing from before the drop.

        Bounded retries (`reconnect_attempts` every `reconnect_wait_s`), then the same reconcile as at start: the
        state file against IB's positions and open orders. A problem there, a non-paper account on the new
        connection, or no connection at all halts; a clean result carries on. Orders resting at IB (the stop and
        target are GTC) kept protecting the position while we were away. Without a `reconnect` callable this does nothing.
        """
        if self.reconnect is None or self.ib.isConnected():
            return
        log.error("connection to IB lost")
        say("CONNECTION: lost. Reconnecting; nothing is traded until the state has been checked against IB again.")
        for attempt in range(1, self.reconnect_attempts + 1):
            try:
                self.reconnect()
                break
            except PaperGuardError as e:
                self._halt(f"paper guard on reconnect: {e}")
                return
            except Exception as e:
                log.warning("reconnect attempt %d of %d failed: %s", attempt, self.reconnect_attempts, e)
                say(f"CONNECTION: attempt {attempt} of {self.reconnect_attempts} failed ({type(e).__name__}: {e})")
                self.ib.sleep(self.reconnect_wait_s)
        else:
            self._halt(f"connection to IB lost and {self.reconnect_attempts} reconnect attempts failed")
            return
        say("CONNECTION: back. Checking the state against IB.")
        self.ib.qualifyContracts(self.contract)
        res = reconcile(self.ib, self.broker, self.state)
        self._report(res)
        log.info("connection to IB restored; reconcile %s", "OK" if res.ok else "found problems")
        if res.problems:
            self._halt("reconcile after reconnect: " + "; ".join(res.problems))
            return
        try:
            if res.needs_protection:
                say("            did: no stop/target found at IB for the open position, placing a new one")
                self._protect_or_flatten(self.state.position, self._ref_price())
        except ExecutionHalt as e:
            self._halt(str(e))
            return
        self._save()

    def tick(self) -> None:
        if self._stop_seen():
            return
        if self.state.halted:
            log.error("halted, not trading: %s", self.state.halted)
            return
        self._ensure_connected()
        if self.state.halted:
            return
        now = self.clock()
        try:
            self._tick(now)
            self._errors = 0
        except ExecutionHalt as e:
            self._halt(str(e))
        except PaperGuardError as e:
            self._halt(f"paper guard: {e}")
        except Exception as e:  # a failed poll (feed error, dropped connection): try again, but the deadline cannot wait
            self._errors += 1
            log.exception("poll failed (%d of %d): %s", self._errors, MAX_TICK_ERRORS, e)
            try:
                self._wall_clock_exit(self._last_ref, now)
            except Exception as inner:
                log.exception("wall-clock exit after a failed poll also failed: %s", inner)
                self._halt(f"wall-clock exit failed: {inner}")
                return
            if self._errors >= MAX_TICK_ERRORS:
                self._halt(f"{self._errors} failed polls in a row; last: {e}")

    def _tick(self, now: pd.Timestamp) -> None:
        closed = self.feed.poll()
        if closed.empty:
            self._wall_clock_exit(self._last_ref, now)
            return
        self._last_ref = float(closed["close"].iloc[-1])
        self._sync_position(now)
        if self.state.halted:
            return
        if self.state.last_bar_ts is None:  # first run: start from now, never trade on history
            back = self.catch_up if self.dry_run else 0
            anchor = closed.index[max(0, len(closed) - 1 - back)]
            self.state.last_bar_ts = anchor.isoformat()
            self._save()
            log.info("first run: starting after bar %s", anchor)
            say(f"first run : starting after the bar of {self._et(anchor.isoformat())}; no trades on history")
        last = pd.Timestamp(self.state.last_bar_ts)
        wait_for = self._hole_to_wait_for(last, now)
        for ts in closed.index[closed.index > last]:
            if wait_for is not None and ts > wait_for:
                log.warning("waiting for missing bar %s before stepping %s", wait_for, ts)
                break
            self._process_bar(closed, ts, now)
            if self.state.halted:
                return
        self._wall_clock_exit(self._last_ref, now)

    def _hole_to_wait_for(self, last: pd.Timestamp, now: pd.Timestamp) -> pd.Timestamp | None:
        """The earliest missing bar after `last` that is still within its grace period (a late bar is not a lost one)."""
        waiting = [h for h in self.feed.holes if h > last and now - (h + BAR) < self.hole_wait]
        return min(waiting, default=None)

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
            if (oid in (pos.stop_order_id, pos.target_order_id) and owns(self.tag, trade.order.orderRef)
                    and trade.orderStatus.status == "Filled"):
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
        where, late = position_text(st.position), now - bar_close
        try:
            outcome = "no signal" if intent is None else self._act(intent, ts, bars, now)
        except ExecutionHalt as e:
            say(bar_line(ts, closed.loc[ts], late, where, f"HALT: {e}"))
            raise
        say(bar_line(ts, closed.loc[ts], late, where, outcome))
        if not st.halted:
            st.last_bar_ts = ts.isoformat()
            self._save()

    def _act(self, intent: Entry | Exit, ts: pd.Timestamp, bars: pd.DataFrame, now: pd.Timestamp) -> str:
        """Act on a decision. Returns one line saying what was decided and what was done, for the console."""
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
                record["action"] = f"skipped: {blocked or 'size 0 (stop not below the price, or no room under the risk limits)'}"
                self._append("signals.jsonl", record)
                log.info("signal %s entry skipped: %s", ts, record["action"])
                return f"ENTRY signal ({intent.reason}, stop {intent.stop:.2f}) -> {record['action']}"
            order_ref = entry_ref(self.tag, ts)
            try:
                entered = self.executor.enter(
                    order_ref, shares, ref, intent.stop, record["target"], known_refs=st.sent_refs,
                    before_send=lambda: self._write_ahead(order_ref, shares, intent, record["target"], ts, bar_close))
            except RiskRefused as refused:  # already logged by the risk gate; the signal is skipped, nothing halts
                st.pending_entry = None
                record["action"] = f"skipped: risk: {refused.reason}"
                self._append("signals.jsonl", record)
                self._save()
                return f"ENTRY signal ({intent.reason}, stop {intent.stop:.2f}) -> REFUSED by the risk limits: {refused.reason}"
            except DuplicateEntry as dup:
                record["action"] = f"skipped: already sent ({order_ref})"
                self._append("signals.jsonl", record)
                if dup.in_ib:  # IB shows it, the state file does not: a person must look
                    raise ExecutionHalt(f"entry {order_ref} is already at IB but the state has no position: {dup.found}")
                return f"ENTRY signal ({intent.reason}) -> already sent as {order_ref}, not sending again"
            fill = entered.fill if entered else None
            if not self.dry_run:
                st.pending_entry = None  # the outcome is known: it goes into `position` (or nowhere) below
            record["action"] = "bought" if fill else "not filled" if not self.dry_run else "dry run"
            self._append("signals.jsonl", record)
            signal = f"ENTRY signal ({intent.reason}, stop {intent.stop:.2f}, target {record['target'] or 'none'})"
            if fill is None:
                self._save()
                return f"{signal} -> {record['action']}" + ("" if self.dry_run else f", {shares} shares not filled in time")
            st.position = PositionState(shares=fill.shares, entry_time=bar_close.isoformat(), entry_price=fill.price,
                                        stop=intent.stop, target=record["target"], stop_order_id=entered.stop_order_id,
                                        target_order_id=entered.target_order_id)
            self._save()  # the position exists at IB now: write it down before anything else can fail
            if fill.price <= intent.stop:  # the backtester drops an entry whose fill is not above the stop; so do we
                self._abort_entry(f"filled at {fill.price}, not above the stop {intent.stop}", ref, now)
                return f"{signal} -> bought {fill.shares} @ {fill.price:.2f}, not above the stop: sold again"
            if not entered.protected:  # IB refused the bracket's stop: place the pair on its own, or sell
                self._protect_or_flatten(st.position, ref, now)
            self._save()
            if st.position is None:
                return f"{signal} -> bought {fill.shares} @ {fill.price:.2f}, protection failed: sold again"
            return f"{signal} -> bought {fill.shares} @ {fill.price:.2f}, stop and target resting at IB"
        # Exit: never skipped because it is old.
        pos = st.position
        fill = self.executor.exit(pos, ref)
        record["action"] = "sold" if fill else "dry run" if self.dry_run else "nothing left to sell"
        self._append("signals.jsonl", record)
        signal = f"EXIT signal ({intent.reason})"
        if self.dry_run:
            return f"{signal} -> dry run, nothing sent"
        if fill is not None:
            self._record_trade(pos, exit_time=bar_close, exit_price=fill.price, reason=intent.reason, fill_time=fill.time)
            outcome = f"{signal} -> sold {fill.shares} @ {fill.price:.2f}"
        else:  # the stop or target got there first
            when, price, why = self._closing_info(pos, now)
            self._record_trade(pos, exit_time=pd.Timestamp(when), exit_price=price, reason=why)
            outcome = f"{signal} -> nothing left to sell, already closed by the {why}"
        st.position = None
        return outcome

    def _write_ahead(self, order_ref: str, shares: int, intent: Entry, target: float | None,
                     ts: pd.Timestamp, bar_close: pd.Timestamp) -> None:
        """Write the entry's intent to disk before the bracket is sent, so a restart can find it at IB."""
        if self.dry_run:
            return
        st = self.state
        today = order_ref.rsplit(":", 2)[1]  # keep the refs of the last session only; older ones cannot collide
        st.sent_refs = [r for r in st.sent_refs if r.rsplit(":", 2)[1] == today] + [order_ref]
        st.pending_entry = {"ref": order_ref, "shares": shares, "stop": intent.stop, "target": target,
                            "signal_bar": ts.isoformat(), "entry_time": bar_close.isoformat()}
        self._save()

    def _protect_or_flatten(self, pos: PositionState, ref: float, now: pd.Timestamp | None = None) -> None:
        """Rest the stop/target at IB. If that fails, sell the position at once and halt: never run unprotected."""
        try:
            self.executor.protect(pos)
        except Exception as e:
            log.error("protection failed (%s); selling the position", e)
            self._abort_entry(f"protection failed: {e}", ref, now or self.clock())
            self._halt(f"protection failed ({e}); the position was sold")

    def _abort_entry(self, why: str, ref: float, now: pd.Timestamp) -> None:
        """Sell what an entry just bought and forget it (logged as a trade)."""
        pos = self.state.position
        log.warning("entry aborted: %s", why)
        fill = self.executor.exit(pos, ref)
        if fill is not None:
            self._record_trade(pos, exit_time=now, exit_price=fill.price, reason="entry_aborted", fill_time=fill.time)
        else:
            when, price, reason = self._closing_info(pos, now)
            self._record_trade(pos, exit_time=pd.Timestamp(when), exit_price=price, reason=reason)
        self.state.position = None
        self._save()

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

    def _wall_clock_exit(self, ref: float | None, now: pd.Timestamp) -> None:
        pos = self.state.position
        if not self.wall_clock_rules or pos is None or pos.closed_at or self.dry_run:
            return
        carried = pd.Timestamp(pos.entry_time).tz_convert(NY).date() != now.tz_convert(NY).date()
        deadline = self._deadline(now)
        if not carried and (deadline is None or now < deadline):
            return
        why = "overnight" if carried else "flatten_deadline"
        if ref is None:
            raise ExecutionHalt(f"{why}: {pos.shares} {self.symbol} must be sold but no price is known yet")
        log.warning("wall clock: closing %d %s (%s)", pos.shares, self.symbol, why)
        fill = self.executor.exit(pos, ref)
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
            try:
                self.start()
                while True:
                    self.tick()
                    if once or self.state.halted or self.stopped:
                        break
                    self.ib.sleep(POLL_SECONDS)
            except StopFileError as e:
                self.stopped = True
                log.error("%s", e)
                say(f"STOP      : {e}")
            except KeyboardInterrupt:
                self._interrupted()

    def _interrupted(self) -> None:
        """Ctrl+C: write the state down and say what is still resting at IB. No traceback."""
        if self.state is not None:
            try:
                self._save()
            except Exception as e:  # never let the goodbye fail loudly
                log.error("could not save the state on Ctrl+C: %s", e)
        pos = self.state.position if self.state else None
        log.warning("stopped by Ctrl+C")
        say("Ctrl+C    : stopped. State saved.")
        if pos is not None and not self.dry_run:
            say(f"            Still {position_text(pos)}. Its stop/target stay resting at IB (good till cancelled) "
                "and are checked again on the next start.")
        say("            If an order was in flight, the next start's reconcile finds it and halts or repairs.")
