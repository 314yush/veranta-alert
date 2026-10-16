"""Native position charts: official candles, verified reference fallback, explicit levels."""
from __future__ import annotations
import asyncio
import math
import time
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from .ohlc import fetch_candles
from .formatting import display_pair

COLORS = {'ENTRY':'#91b9ff','MARK':'#ffffff','LIQ':'#ff6868','TP':'#58e6ac','SL':'#ffbc69'}
FONTS = Path(__file__).resolve().parent.parent/'assets/fonts'


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
    im=Image.new('RGB',(1100,680),'#090d13');d=ImageDraw.Draw(im)
    def font(size,bold=False):
        return ImageFont.truetype(str(FONTS/('Geist-SemiBold.ttf' if bold else 'Geist-Regular.ttf')),size)
    d.text((30,22),'veranta alerts',fill='#9ba9bc',font=font(17))
    d.text((30,54),f'{display_pair(pos.symbol)}  {pos.side}',fill='white',font=font(30,True))
    pnl=pos.unrealized_pnl_usdc
    label=f'Net PnL {pnl:+.2f} USDC' if pnl is not None else 'Net PnL unavailable'
    if pos.unrealized_pnl_pct is not None:label+=f'  ({pos.unrealized_pnl_pct:+.1f}%)'
    d.text((30,97),label,fill='#58e6ac' if pnl is not None and pnl>=0 else '#ff7777',font=font(22))
    d.text((30,135),source[:105],fill='#9ba9bc',font=font(15))
    x0,x1,y0,y1=35,820,185,540
    d.rounded_rectangle((x0-10,y0-12,1070,y1+22),radius=10,fill='#111720')
    values=[float(v) for c in candles for v in (c['low'],c['high'])]
    if not values:
        values=[v for v in (pos.open_price,pos.mark_price,pos.liquidation_price,pos.tp,pos.sl) if v and v>0]
    if not values:values=[0.99,1.01]
    lo,hi=min(values),max(values)
    span=max(hi-lo,hi*.0005)
    # Keep nearby entry/liquidation visible, but don't compress candles for a far TP.
    if candles:
        for v in (pos.open_price,pos.liquidation_price,pos.mark_price):
            if v and lo-span<=v<=hi+span:lo=min(lo,v);hi=max(hi,v)
    pad=max((hi-lo)*.10,hi*.0001);lo-=pad;hi+=pad
    def py(v):return y1-(min(hi,max(lo,v))-lo)/(hi-lo)*(y1-y0)
    for k in range(5):
        yy=y0+(y1-y0)*k/4
        d.line((x0,yy,x1,yy),fill='#27303d')
    if candles:
        step=(x1-x0)/max(1,len(candles));width=max(2,min(9,step*.65))
        for i,c in enumerate(candles):
            x=x0+step*(i+.5);color='#58e6ac' if c['close']>=c['open'] else '#ff6868'
            d.line((x,py(c['high']),x,py(c['low'])),fill=color,width=1)
            a,b=sorted((py(c['open']),py(c['close'])))
            d.rectangle((x-width/2,a,x+width/2,max(a+2,b)),fill=color)
        d.text((x0,y1+30),time.strftime('%H:%M UTC',time.gmtime(candles[0]['time'])),font=font(14),fill='#9ba9bc')
        d.text((x1-100,y1+30),time.strftime('%H:%M UTC',time.gmtime(candles[-1]['time'])),font=font(14),fill='#9ba9bc')
    else:
        d.text((x0+20,y0+25),'Price history unavailable · current levels shown',fill='#9ba9bc',font=font(18))
    levels=sorted(level_layout(pos,lo,hi),key=lambda x:py(x['price']))
    # Reserve distinct label rows even when high-leverage levels cluster together.
    ys=[]
    for level in levels:ys.append(max(py(level['price']),ys[-1]+48 if ys else y0))
    if ys and ys[-1]>y1:
        ys[-1]=y1
        for i in range(len(ys)-2,-1,-1):ys[i]=min(ys[i],ys[i+1]-48)
    for level,label_y in zip(levels,ys):
        y=py(level['price']);color=COLORS[level['label']]
        for x in range(x0,x1,14):d.line((x,y,min(x+8,x1),y),fill=color,width=2)
        d.line((x1,y,850,label_y),fill=color,width=1)
        suffix=' ↑ off-scale' if level['outside']=='above' else ' ↓ off-scale' if level['outside']=='below' else ''
        d.text((858,label_y-19),level['label']+suffix,fill=color,font=font(14,True))
        d.text((858,label_y+1),price(level['price']),fill=color,font=font(18,True))
    d.text((30,600),f'Entry {price(pos.open_price)}   ·   TP {price(pos.tp) if pos.tp else "not set"}   ·   SL {price(pos.sl) if pos.sl else "not set"}',fill='white',font=font(18))
    d.text((30,634),f'Collateral ${pos.collateral_usdc:g}   ·   Leverage {pos.leverage:g}×   ·   Liquidation {price(pos.liquidation_price)}',fill='#9ba9bc',font=font(16))
    buf=BytesIO();im.save(buf,format='PNG');return buf.getvalue()


async def build_chart(client,pos):
    now=int(time.time());candles=[];source='History unavailable · current Veranta levels'
    try:
        raw=await asyncio.wait_for(client.client.markets.candles(pos.symbol,'15',now-86400,now),5)
        candles=normalize_candles(raw)
        if candles:source='Veranta candles · 15 min · live Veranta price levels'
    except Exception:
        pass
    if not candles and pos.chart_symbol:
        raw=await asyncio.to_thread(fetch_candles,pos.chart_symbol,limit=72)
        candles=normalize_candles(raw)
        if candles and pos.mark_price:
            # Don't plot a different price regime as if it were this product.
            if abs(candles[-1]['close']/pos.mark_price-1)>.05:candles=[]
        if candles:source=f'{pos.chart_symbol} reference candles · 15 min · levels use Veranta prices'
    if not candles:
        from .ticks import load_ticks
        ticks=await asyncio.to_thread(load_ticks,pos.pair_index)
        buckets={}
        for ts,p in ticks:
            t=int(ts)//60*60
            c=buckets.setdefault(t,dict(time=t,open=p,high=p,low=p,close=p))
            c.update(high=max(c['high'],p),low=min(c['low'],p),close=p)
        candles=normalize_candles(list(buckets.values()))
        if candles:source='Observed Veranta prices · 1 min candles · partial local history'
    return await asyncio.to_thread(render_chart,pos,candles,source)
