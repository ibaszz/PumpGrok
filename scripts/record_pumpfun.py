"""Record pump.fun curve ticks from Solana RPC logs into a pumpgrok replay dataset.

Source: `logsSubscribe` on the pump.fun program. Every `Program data:` line is decoded by
`pumpgrok.tools.onchain.pump_events`; create events supply token evidence, trade events
supply curve reserves and buyer counts. Output is the fixture shape that
`pumpgrok run --once --replay <dir>` and `pumpgrok backtest --fixture <dir>` already read,
plus a sealed manifest.json with per-file sha256.

Usage:
    uv run python scripts/record_pumpfun.py --out var/recordings/live-01 --duration 600
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import websockets

from pumpgrok.replay.clock import format_ts_utc
from pumpgrok.tools.onchain.pump_events import (
    CompleteEvent,
    CreateEvent,
    EventDecodeError,
    TradeEvent,
    decode_program_data,
)

PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
WS_URL = os.environ.get("PUMPFUN_RPC_WS", "wss://api.mainnet-beta.solana.com")
# Free PumpPortal streams only (creates + migrations). The metered trade stream is not
# touched, so this needs no API key and costs nothing.
PP_URL = os.environ.get("PUMPPORTAL_WS", "wss://pumpportal.fun/api/data")


def _epoch(value: int) -> float:
    """Chain timestamps arrive as ms when large; normalize to epoch seconds."""
    return value / 1000.0 if value > 100_000_000_000 else float(value)


class Recorder:
    def __init__(self, out: Path, max_tokens: int, rotate_after: float = 0.0) -> None:
        self.out = out
        self.max_tokens = max_tokens
        self.rotate_after = rotate_after
        self.last: dict[str, float] = {}
        self.ticks = out / "events.jsonl"
        self.tokens: dict[str, dict[str, Any]] = {}
        self.buyers: dict[str, set[str]] = {}
        self.last_ts = 0.0
        self.count = 0
        self.seen_sigs: set[str] = set()
        # Side channels: the RPC decode has no creator field and never says when a curve
        # completed, so those land in their own files and leave events.jsonl untouched.
        self.creators = out / "creators.jsonl"
        self.migrations = out / "migrations.jsonl"
        self.n_creators = 0
        self.n_pumpportal = 0
        self.n_migrations = 0

    def _emit(self, mint: str, ts: float, sol: int, tok: int, complete: bool) -> None:
        meta = self.tokens.get(mint)
        if meta is None:
            return  # no create evidence seen: not a recordable candidate
        stamp = max(ts, self.last_ts + 1e-6)
        record = {
            "ts_utc": format_ts_utc(stamp),
            "kind": "tick",
            "mint": mint,
            "name": meta["name"],
            "image_uri": meta["uri"],
            "created_ts_utc": meta["created_ts_utc"],
            "unique_buyers": len(self.buyers.get(mint, ())),
            "S": int(sol),
            "T": int(tok),
            "complete": complete,
            "source": "rpc-logs",
        }
        with self.ticks.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        self.last_ts = stamp
        self.last[mint] = stamp
        self.count += 1

    def handle(self, event: Any) -> None:
        if isinstance(event, CreateEvent):
            if len(self.tokens) >= self.max_tokens:
                if not self._evict_idle():
                    return
            self.tokens[event.mint] = {
                "name": event.name,
                "uri": event.uri,
                "created_ts_utc": format_ts_utc(_epoch(event.timestamp)),
                "complete": False,
                "creator": event.creator,
            }
            # The decode already carries the creator wallet: record it at 100% coverage
            # rather than waiting for the throttled PumpPortal create stream.
            self._append(
                self.creators,
                {
                    "ts_utc": format_ts_utc(_epoch(event.timestamp)),
                    "mint": event.mint,
                    "creator": event.creator,
                    "deployer": event.user,
                    "bonding_curve": event.bonding_curve,
                    "source": "rpc-logs",
                },
            )
            self.n_creators += 1
            self.buyers.setdefault(event.mint, set())
            self._emit(
                event.mint,
                _epoch(event.timestamp),
                event.virtual_sol_reserves,
                event.virtual_token_reserves,
                False,
            )
        elif isinstance(event, TradeEvent):
            if event.mint not in self.tokens:
                return
            if event.is_buy:
                self.buyers.setdefault(event.mint, set()).add(event.user)
            self._emit(
                event.mint,
                _epoch(event.timestamp),
                event.virtual_sol_reserves,
                event.virtual_token_reserves,
                bool(self.tokens[event.mint]["complete"]),
            )
        elif isinstance(event, CompleteEvent):
            if event.mint in self.tokens:
                self.tokens[event.mint]["complete"] = True

    def _evict_idle(self) -> bool:
        """Free one slot by dropping the longest-idle mint, so a long run keeps recording."""
        if self.rotate_after <= 0 or not self.last:
            return False
        mint, seen = min(self.last.items(), key=lambda kv: kv[1])
        if self.last_ts - seen < self.rotate_after:
            return False
        self.tokens.pop(mint, None)
        self.buyers.pop(mint, None)
        del self.last[mint]
        return True

    def handle_pumpportal(self, msg: dict[str, Any]) -> None:
        """One free-stream message: a token creation or a migration."""
        mint = msg.get("mint")
        if not mint:
            return
        tx = msg.get("txType")
        record = {
            "ts_utc": format_ts_utc(time.time()),
            "mint": mint,
            "signature": msg.get("signature"),
        }
        if tx == "create":
            record.update(
                source="pumpportal-free",
                creator=msg.get("traderPublicKey"),
                pool=msg.get("pool"),
                sol_in_pool=msg.get("solInPool"),
                tokens_in_pool=msg.get("tokensInPool"),
                market_cap_sol=msg.get("marketCapSol"),
                initial_buy=msg.get("initialBuy"),
            )
            self._append(self.creators, record)
            self.n_creators += 1
            self.n_pumpportal += 1
        elif tx == "migrate":
            record.update(pool=msg.get("pool"))
            self._append(self.migrations, record)
            self.n_migrations += 1

    @staticmethod
    def _append(path: Path, record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")

    def seal(self) -> dict[str, Any]:
        data = self.ticks.read_bytes()
        lines = data.splitlines()
        first = json.loads(lines[0])["ts_utc"] if lines else ""
        last = json.loads(lines[-1])["ts_utc"] if lines else ""
        # Distinct mints in the file, not len(self.tokens): that dict only holds
        # the current watchset, so a rotating run undercounts by orders of magnitude.
        mints = len({json.loads(line)["mint"] for line in lines})
        side = {}
        for path in (self.creators, self.migrations):
            if path.exists():
                side[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = {
            "dataset": self.out.resolve().name,
            "recorded_from_utc": first,
            "recorded_to_utc": last,
            "seed_hint": 20260101,
            "sources": {"rpc-logs": WS_URL, "pumpportal-free": PP_URL},
            "sha256": {"events.jsonl": hashlib.sha256(data).hexdigest(), **side},
            "notes": (
                f"Recorded from live pump.fun program logs via logsSubscribe, "
                f"creators/migrations from the free PumpPortal streams. "
                f"{mints} mints with create evidence, {len(lines)} ticks, "
                f"{self.n_creators} creator rows, {self.n_pumpportal} pumpportal creates, "
                f"{self.n_migrations} migrations."
            ),
        }
        (self.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest


async def _consume(ws: Any, rec: Recorder, end: float, max_ticks: int) -> None:
    loop = asyncio.get_running_loop()
    while loop.time() < end and rec.count < max_ticks:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, end - loop.time()))
        except TimeoutError:
            return
        msg = json.loads(raw)
        value = ((msg.get("params") or {}).get("result") or {}).get("value") or {}
        sig = value.get("signature")
        if not sig or sig in rec.seen_sigs:
            continue
        rec.seen_sigs.add(sig)
        for line in value.get("logs") or []:
            if not line.startswith("Program data: "):
                continue
            try:
                rec.handle(decode_program_data(line[len("Program data: ") :]))
            except EventDecodeError:
                continue


async def _pumpportal(rec: Recorder, end: float) -> None:
    """Free PumpPortal streams: creators and migrations, reconnecting until `end`."""
    loop = asyncio.get_running_loop()
    while loop.time() < end:
        try:
            async with websockets.connect(PP_URL, ping_interval=30, open_timeout=20) as ws:
                for method in ("subscribeNewToken", "subscribeMigration"):
                    await ws.send(json.dumps({"method": method}))
                while loop.time() < end:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, end - loop.time()))
                    except TimeoutError:
                        return
                    msg = json.loads(raw)
                    if msg.get("txType"):
                        rec.handle_pumpportal(msg)
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            print(f"pumpportal reconnect: {type(exc).__name__}", file=sys.stderr)
            await asyncio.sleep(2.0)


async def run(
    out: Path, duration: float, max_tokens: int, max_ticks: int, rotate_after: float
) -> Recorder:
    rec = Recorder(out, max_tokens, rotate_after)
    out.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    end = loop.time() + duration
    reconnects = 0
    side = asyncio.create_task(_pumpportal(rec, end))
    try:
        while loop.time() < end and rec.count < max_ticks:
            try:
                async with websockets.connect(
                    WS_URL, ping_interval=30, open_timeout=20, max_size=20_000_000
                ) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "logsSubscribe",
                                "params": [{"mentions": [PROGRAM]}, {"commitment": "confirmed"}],
                            }
                        )
                    )
                    await _consume(ws, rec, end, max_ticks)
            except (OSError, websockets.exceptions.WebSocketException) as exc:
                reconnects += 1
                print(f"reconnect {reconnects}: {type(exc).__name__}", file=sys.stderr)
                await asyncio.sleep(2.0)
            else:
                if loop.time() >= end or rec.count >= max_ticks:
                    break
    finally:
        side.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await side
        if rec.count:
            rec.seal()  # sealed even on a hard drop, so a partial recording stays usable
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--duration", type=float, default=600.0)
    ap.add_argument("--max-tokens", type=int, default=100)
    ap.add_argument("--max-ticks", type=int, default=500_000)
    ap.add_argument("--rotate-after", type=float, default=0.0)
    args = ap.parse_args()
    rec = asyncio.run(
        run(args.out, args.duration, args.max_tokens, args.max_ticks, args.rotate_after)
    )
    if rec.count == 0:
        print("no ticks recorded; seal refused", file=sys.stderr)
        return 1
    manifest = rec.seal()
    print(json.dumps({"ticks": rec.count, "mints": len(rec.tokens), **{
        k: manifest[k] for k in ("dataset", "recorded_from_utc", "recorded_to_utc")
    }}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
