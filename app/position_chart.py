"""Native position charts: official candles, verified reference fallback, explicit levels."""
from __future__ import annotations
import asyncio
import math
import time
from .ohlc import fetch_candles



def normalize_candles(data):
    if isinstance(data,dict) and 't' in data:
        data=[dict(zip(('time','open','high','low','close'),v)) for v in zip(*(data.get(k,[]) for k in ('t','o','h','l','c')))]
    if not isinstance(data,list):return []
    rows={}
    for row in data:
        try:
            item={k:float(row[k]) for k in ('time','open','high','low','close')}
            if not all(math.isfinite(v) and v>0 for v in item.values()):continue
            if item['time']>1e12:item['time']/=1000
            if item['time']>time.time()+300:continue
            if item['low']>min(item['open'],item['close']) or item['high']<max(item['open'],item['close']):continue
            rows[item['time']]=item
        except (KeyError,ValueError,TypeError):continue
    out=sorted(rows.values(),key=lambda r:r['time'])[-72:]
    return out if out and time.time()-out[-1]['time']<=1800 else []


def level_layout(pos, low, high):
    levels=[]
    for name,value in [('ENTRY',pos.open_price),('MARK',pos.mark_price),('LIQ',pos.liquidation_price),('TP',pos.tp),('SL',pos.sl)]:
        if value is not None and value>0 and math.isfinite(value):
            levels.append(dict(label=name,price=value,outside='above' if value>high else 'below' if value<low else None))
    return levels


def price(value):
    if value is None:return '—'
    return f'{value:,.2f}' if value>=10 else f'{value:.6f}'.rstrip('0').rstrip('.')


def render_chart(pos, candles, source):
    from .ticket import render_ticket
    return render_ticket(
        pair_label=pos.symbol, side=pos.side, mark=pos.mark_price,
        open_price=pos.open_price, liquidation=pos.liquidation_price,
        tp=pos.tp, sl=pos.sl, pnl_usdc=pos.unrealized_pnl_usdc,
        pnl_pct=pos.unrealized_pnl_pct, collateral=pos.collateral_usdc,
        leverage=pos.leverage, is_long=pos.is_long,
        buffer_pts=pos.buffer_to_liq_pts(), ticks=[],
        candle_interval_s=300, window_s=7200,
        chart_candles=candles, chart_label=source,
    )


async def build_chart(client,pos):
    from .ticks import load_ticks
    from .candles import build_candles, prepare_tape
    now=int(time.time())
    ticks=await asyncio.to_thread(load_ticks,pos.pair_index,hours=2,limit=0)
    ticks=prepare_tape(ticks,now=now)
    candles=[]
    source='HISTORY UNAVAILABLE'
    # Prefer the traded feed; never discard extrema before OHLC aggregation.
    if ticks and now-ticks[-1][0]<=15:
        candles=build_candles(ticks,300)
        source='LOCAL OBSERVATIONS'
    if not candles:
        try:
            raw=await asyncio.wait_for(client.client.markets.candles(pos.symbol,'5',now-7200,now),5)
            candles=normalize_candles(raw)
            candles=[c for c in candles if c['time']>=now-7500]
            if candles:source='VERANTA MARKET CANDLES'
        except Exception:
            pass
    if not candles and pos.chart_symbol:
        raw=await asyncio.to_thread(fetch_candles,pos.chart_symbol,interval='5m',limit=24)
        candles=normalize_candles(raw)
        if candles and pos.mark_price:
            if abs(candles[-1]['close']/pos.mark_price-1)>.05:candles=[]
        if candles:source=f'{pos.chart_symbol} SPOT REFERENCE'
    if not candles and ticks:
        candles=build_candles(ticks,300)
        source=f'LOCAL OBSERVATIONS · LAST UPDATE {int(now-ticks[-1][0])}s AGO'
    return await asyncio.to_thread(render_chart,pos,candles,source)
