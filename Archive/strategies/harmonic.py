from dataclasses import dataclass
from typing import Optional

@dataclass
class HarmonicSignal:
    symbol: str
    pattern: str
    side: str
    price: float
    prz_low: Optional[float] = None
    prz_high: Optional[float] = None
    rsi: Optional[float] = None
    supertrend: Optional[str] = None  # "up"/"down"/None
    risk_reward: Optional[str] = "1:2"

def _rr_to_float(rr: str) -> float:
    try:
        a, b = rr.split(":")
        return float(b) / float(a)
    except Exception:
        return 2.0

def compute_sl_tp(sig: HarmonicSignal):
    price = sig.price
    rr = _rr_to_float(sig.risk_reward or "1:2")
    buffer = price * 0.0025  # 0.25% si PRZ absent

    if sig.side.upper() == "BUY":
        sl = sig.prz_low if sig.prz_low is not None else (price - buffer)
        tp = price + rr * (price - sl)
    else:
        sl = sig.prz_high if sig.prz_high is not None else (price + buffer)
        tp = price - rr * (sl - price)
    return round(sl, 1), round(tp, 1)

def basic_confirmations(sig: HarmonicSignal) -> bool:
    if sig.side.upper() == "BUY":
        if sig.supertrend and sig.supertrend.lower() != "up":
            return False
        if sig.rsi is not None and not (35 <= sig.rsi <= 60):
            return False
        return True
    else:
        if sig.supertrend and sig.supertrend.lower() != "down":
            return False
        if sig.rsi is not None and not (40 <= sig.rsi <= 65):
            return False
        return True