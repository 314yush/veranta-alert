"""Read-only active TWAP display models.

Wire format verified against /twap/api-json and /twap/twaps. Only confirmed
slice records count as fills; elapsed time never implies completion.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActiveTwap:
    twap_id: int
    wallet: str
    pair_index: int
    symbol: str
    side: str
    opening: bool
    total_slices: int
    filled_slices: int
    # Position slot -> latest confirmed execution time in seconds.
    position_times: dict[int, float]

    def matches(self, pos):
        executed = self.position_times.get(pos.index)
        return (pos.trader.lower() == self.wallet and pos.pair_index == self.pair_index
                and pos.side == self.side and pos.opened_at is not None
                and executed is not None and executed >= pos.opened_at)


@dataclass
class PositionRow:
    position: object | None = None
    twaps: list[ActiveTwap] = field(default_factory=list)


def parse_active_twaps(rows, wallet, symbols, *, now=None):
    now = time.time() if now is None else now
    result = {}
    for item in rows:
        if not isinstance(item, dict) or not isinstance(item.get('twap'), dict):
            raise ValueError('Malformed TWAP response')
        raw = item['twap']
        if str(raw.get('trader', '')).lower() != wallet.lower():
            continue
        if raw.get('canceled') is True or raw.get('completed') is True:
            continue
        # Unknown statuses are not silently treated as active or absent.
        if raw.get('canceled') is not False or raw.get('completed') is not False:
            raise ValueError('Missing TWAP status')
        try:
            key, pair, total = int(raw['twapId']), int(raw['pairIndex']), int(raw['totalSlices'])
            started = float(raw['timestampMillis'])/1000
            if not math.isfinite(started) or total <= 0 or key < 0 or pair < 0:
                raise ValueError('Invalid TWAP identity or schedule')
            if started > now:
                continue
            if not isinstance(raw['buy'], bool) or not isinstance(raw['isOpeningTwap'], bool):
                raise ValueError('Missing TWAP direction')
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Malformed active TWAP') from exc
        trades = item.get('trades')
        if not isinstance(trades, list):
            raise ValueError('Missing TWAP executions')
        slices, position_times = set(), {}
        for fill in trades:
            if not isinstance(fill, dict):
                continue
            try:
                if (int(fill['twapId']) != key or int(fill['pairIndex']) != pair
                        or str(fill['trader']).lower() != wallet.lower()
                        or fill.get('isOpeningFlow') is not raw['isOpeningTwap']):
                    continue
                tx = fill.get('transactionHash')
                if not isinstance(tx, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', tx):
                    continue
                ts = float(fill['timestampMillis'])/1000
                sid, index = int(fill['sliceId']), int(fill['index'])
                if not math.isfinite(ts) or not started <= ts <= now+5 or not 1 <= sid <= total or index < 0:
                    continue
                slices.add(sid)
                position_times[index] = max(ts, position_times.get(index, 0))
            except (KeyError, TypeError, ValueError):
                continue
        if len(slices) >= total:
            continue
        result[key] = ActiveTwap(key,wallet.lower(),pair,symbols.get(pair,f'PAIR#{pair}'),
                                 'LONG' if raw['buy'] else 'SHORT',raw['isOpeningTwap'],
                                 total,len(slices),position_times)
    return list(result.values())


def position_rows(positions, twaps=()):
    """Attach progress to real positions; unmatched orders never count as positions."""
    rows = [PositionRow(position=p) for p in positions]
    for twap in twaps:
        matching = [row for row in rows if row.position is not None and twap.matches(row.position)]
        if matching:
            for row in matching:
                row.twaps.append(twap)
        else:
            rows.append(PositionRow(twaps=[twap]))
    return rows


def progress_label(twap):
    action = 'opening' if twap.opening else 'closing'
    state = 'partially filled' if twap.filled_slices else 'filling'
    return f'TWAP {action} · {state} · {twap.filled_slices}/{twap.total_slices} slices'
