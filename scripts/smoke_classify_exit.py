"""Unconfirmed closes must never be labelled as a known execution cause."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.alerts import classify_exit
from app.veranta_sdk import Position
p = Position(trader='0x'+'11'*20, pair_index=1, index=0, is_long=True,
             collateral_usdc=100, leverage=10, open_price=100, tp=110, sl=95,
             liquidation_price=91.5)
for mark in (None, 80, 91.5, 95, 100, 110, 120):
    assert classify_exit(p, mark) == 'closed'
print('Unconfirmed exit classification: OK')
