"""Generate simulated duration cards without changing the bot's default timeframe."""
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ticket import render_ticket
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / 'data/chart-previews/durations'
out.mkdir(parents=True, exist_ok=True)
# All examples end at the same time; seeded data makes the comparison reproducible.
now = int(time.time()) - 1
specs = [('2-minute', 120, 5, '2 MIN', .35),
         ('5-minute', 300, 15, '5 MIN', .65),
         ('10-minute', 600, 30, '10 MIN', 1.1),
         ('10-hour', 36000, 1800, '10 HR', 8.0)]
for name, duration, interval, label, amplitude in specs:
    rng = random.Random(45)
    tape = []
    walk = 0
    for i in range(1201):
        t = i/1200
        walk += rng.gauss(0, .025)
        price = 4165 + amplitude*(math.sin(t*15)*.35 + math.sin(t*53)*.09 + walk*.32 + t*.8)
        tape.append((now-duration+duration*t, price))
    entry, mark = tape[0][1], tape[-1][1]
    collateral, leverage = 10000, 33
    pnl = (mark-entry)/entry * collateral*leverage
    png = render_ticket(pair_label='XAU', side='LONG', mark=mark,
                        open_price=entry, liquidation=entry*(1-.85/leverage),
                        tp=entry+amplitude*1.8, sl=entry-amplitude*.7,
                        pnl_usdc=pnl, pnl_pct=pnl/collateral*100,
                        collateral=collateral, leverage=leverage, ticks=tape,
                        candle_interval_s=interval, window_s=duration,
                        chart_label=f'{label} TRADE  ·  SIMULATED')
    assert png
    (out / f'{name}.png').write_bytes(png)

# Comparison sheet retains legible full-sized individual cards alongside it.
canvas = Image.new('RGB', (2160, 1560), '#07080A')
draw = ImageDraw.Draw(canvas)
font = ImageFont.truetype(str(ROOT/'assets/fonts/Geist-SemiBold.ttf'), 28)
for i, (name, duration, interval, label, _) in enumerate(specs):
    x, y = i%2*1080, i//2*780
    bar_label = f'{interval}s' if interval < 60 else f'{interval//60}m'
    draw.text((x+30,y+15), f'{label} TRADE / {bar_label} CANDLES', font=font, fill='#ECEEF3')
    with Image.open(out/f'{name}.png') as card:
        canvas.paste(card, (x,y+60))
canvas.save(out/'comparison.png')
print(out)
