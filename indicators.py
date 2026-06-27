"""
Bot Trading Nexus — Indicadores de Scalping
Optimizados para timeframes curtos: 1m, 3m, 5m
"""
from __future__ import annotations
import time
import numpy as np
import pandas as pd
from dataclasses import dataclass, field


@dataclass
class TFSignal:
    timeframe: str
    direction: str       # "LONG" | "SHORT" | "NEUTRAL"
    score: float         # 0–100
    rsi: float = 0.0
    ema_fast: float = 0.0
    ema_mid: float = 0.0
    ema_slow: float = 0.0
    vwap: float = 0.0
    bb_pct: float = 0.0  # 0=fundo banda, 1=topo banda
    adx: float = 0.0
    volume_ratio: float = 1.0
    details: dict = field(default_factory=dict)


# ── EMA ───────────────────────────────────────────────────────────────────────

def ema(series: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    result = np.empty_like(series, dtype=float)
    result[0] = series[0]
    for i in range(1, len(series)):
        result[i] = series[i] * k + result[i - 1] * (1 - k)
    return result


# ── RSI (período curto para scalping) ─────────────────────────────────────────

def rsi(close: np.ndarray, period: int = 7) -> float:
    warmup = period * 3
    if len(close) < warmup + 1:
        return 50.0
    deltas = np.diff(close[-(warmup + 1):])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    # Wilder smoothing (EMA com alpha = 1/period)
    alpha = 1.0 / period
    avg_gain = gains[0]
    avg_loss = losses[0]
    for g, l in zip(gains[1:], losses[1:]):
        avg_gain = avg_gain * (1 - alpha) + g * alpha
        avg_loss = avg_loss * (1 - alpha) + l * alpha
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


# ── VWAP (intraday) ────────────────────────────────────────────────────────────

def vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray,
         volume: np.ndarray, timestamps: np.ndarray | None = None) -> float:
    if timestamps is not None and len(timestamps) > 0:
        midnight_ms = (int(time.time()) // 86400) * 86400 * 1000
        mask = timestamps >= midnight_ms
        if mask.sum() > 0:
            high, low, close, volume = high[mask], low[mask], close[mask], volume[mask]
    typical = (high + low + close) / 3
    cumvol = volume.sum()
    if cumvol == 0:
        return close[-1]
    return float((typical * volume).sum() / cumvol)


# ── ATR com True Range ────────────────────────────────────────────────────────

def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 7) -> float:
    if len(high) < period + 1:
        return float(np.mean(high[-period:] - low[-period:]))
    n = min(len(high), 50)
    h, l, c = high[-n:], low[-n:], close[-n:]
    tr = np.maximum(h[1:] - l[1:],
         np.maximum(np.abs(h[1:] - c[:-1]),
                    np.abs(l[1:] - c[:-1])))
    return float(_smooth(tr, period)[-1])


# ── Bollinger Bands ────────────────────────────────────────────────────────────

def bollinger_pct(close: np.ndarray, period: int = 10, std_mult: float = 1.5) -> float:
    """Retorna %B: 0.0 = na banda inferior, 1.0 = na banda superior."""
    if len(close) < period:
        return 0.5
    window = close[-period:]
    mid = window.mean()
    std = window.std()
    if std == 0:
        return 0.5
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    pct = (close[-1] - lower) / (upper - lower)
    return float(np.clip(pct, 0.0, 1.0))


# ── ADX ───────────────────────────────────────────────────────────────────────

def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 7) -> float:
    if len(close) < period + 1:
        return 0.0
    n = min(len(close), 50)
    h, l, c = high[-n:], low[-n:], close[-n:]
    tr_arr = np.maximum(h[1:] - l[1:],
                        np.maximum(np.abs(h[1:] - c[:-1]),
                                   np.abs(l[1:] - c[:-1])))
    dm_plus = np.where((h[1:] - h[:-1]) > (l[:-1] - l[1:]),
                       np.maximum(h[1:] - h[:-1], 0), 0)
    dm_minus = np.where((l[:-1] - l[1:]) > (h[1:] - h[:-1]),
                        np.maximum(l[:-1] - l[1:], 0), 0)
    atr_s = _smooth(tr_arr, period)
    dip = _smooth(dm_plus, period) / (atr_s + 1e-9) * 100
    dim = _smooth(dm_minus, period) / (atr_s + 1e-9) * 100
    dx = np.abs(dip - dim) / (dip + dim + 1e-9) * 100
    return float(_smooth(dx, period)[-1])


def _smooth(arr: np.ndarray, period: int) -> np.ndarray:
    result = np.empty_like(arr, dtype=float)
    result[0] = arr[:period].mean() if len(arr) >= period else arr[0]
    k = 1.0 / period
    for i in range(1, len(arr)):
        result[i] = result[i - 1] * (1 - k) + arr[i] * k
    return result


# ── Delta Volume (order flow proxy) ───────────────────────────────────────────

def delta_volume_ratio(open_: np.ndarray, close: np.ndarray,
                       volume: np.ndarray, bars: int = 5) -> float:
    """
    Aproximação de delta volume: candles bull vs bear nos últimos N bars.
    >1.0 = pressão compradora dominante, <1.0 = vendedora.
    """
    if len(close) < bars:
        return 1.0
    o, c, v = open_[-bars:], close[-bars:], volume[-bars:]
    bull_vol = v[c > o].sum()
    bear_vol = v[c <= o].sum()
    total = bull_vol + bear_vol
    if total == 0:
        return 1.0
    return float(bull_vol / total * 2)  # 1.0 = equilíbrio, >1 = bull


# ── Análise completa de um TF ─────────────────────────────────────────────────

def analyze(ohlcv: list, timeframe: str, adx_min: float = 20.0) -> TFSignal:
    """
    ohlcv: lista de candles ccxt [[ts, open, high, low, close, volume], ...]
    Retorna TFSignal com score 0-100 e direção.
    """
    if len(ohlcv) < 30:
        return TFSignal(timeframe=timeframe, direction="NEUTRAL", score=0.0)

    arr = np.array(ohlcv, dtype=float)
    ts_col, o, h, l, c, vol = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4], arr[:, 5]

    # Indicadores
    # EMA fast: 5 em 1m (3 em 1m = 3 minutos de dado = ruído puro)
    ema_fast_p = 5 if timeframe == "1m" else 3
    ema3  = ema(c, ema_fast_p)
    ema8  = ema(c, 8)
    ema21 = ema(c, 21)
    rsi_v = rsi(c, 7)
    vwap_v = vwap(h, l, c, vol, ts_col)
    bb_v  = bollinger_pct(c, 10, 1.5)
    adx_v = adx(h, l, c, 7)
    dv    = delta_volume_ratio(o, c, vol, 5)
    vol_ratio = vol[-1] / (vol[-20:].mean() + 1e-9)

    # Tendência lateral → score 0
    if adx_v < adx_min:
        return TFSignal(timeframe=timeframe, direction="NEUTRAL", score=0.0,
                        rsi=rsi_v, adx=adx_v,
                        ema_fast=ema3[-1], ema_mid=ema8[-1], ema_slow=ema21[-1])

    score_long = 0.0
    score_short = 0.0

    # EMA ribbon (3 > 8 > 21 = bull / 3 < 8 < 21 = bear) ── 30 pts
    if ema3[-1] > ema8[-1] > ema21[-1]:
        score_long += 30
    elif ema3[-1] < ema8[-1] < ema21[-1]:
        score_short += 30
    elif ema3[-1] > ema8[-1]:
        score_long += 15
    elif ema3[-1] < ema8[-1]:
        score_short += 15

    # RSI 7 ── 20 pts
    # Thresholds realistas para scalping — RSI(7) raramente atinge 25/75
    if rsi_v < 35:
        score_long += 20
    elif rsi_v < 45:
        score_long += 10
    elif rsi_v > 65:
        score_short += 20
    elif rsi_v > 55:
        score_short += 10

    # VWAP posição ── 20 pts
    price = c[-1]
    if price > vwap_v * 1.002:
        score_long += 20
    elif price > vwap_v:
        score_long += 10
    elif price < vwap_v * 0.998:
        score_short += 20
    elif price < vwap_v:
        score_short += 10

    # Bollinger %B ── 15 pts
    if bb_v < 0.1:
        score_long += 15
    elif bb_v < 0.25:
        score_long += 7
    elif bb_v > 0.9:
        score_short += 15
    elif bb_v > 0.75:
        score_short += 7

    # Delta Volume ── 15 pts
    if dv > 1.3:
        score_long += 15
    elif dv > 1.1:
        score_long += 7
    elif dv < 0.7:
        score_short += 15
    elif dv < 0.9:
        score_short += 7

    # Volume spike boost direcional ── até +15% só na direcção do volume
    if vol_ratio > 1.5:
        if dv > 1.0:       # volume maioritariamente comprador → boost LONG
            score_long  *= 1.15
        elif dv < 1.0:     # volume maioritariamente vendedor → boost SHORT
            score_short *= 1.15

    score_long  = min(score_long, 100.0)
    score_short = min(score_short, 100.0)

    if score_long > score_short:
        direction = "LONG"
        final_score = score_long
    elif score_short > score_long:
        direction = "SHORT"
        final_score = score_short
    else:
        direction = "NEUTRAL"
        final_score = 0.0

    return TFSignal(
        timeframe=timeframe,
        direction=direction,
        score=round(final_score, 1),
        rsi=rsi_v,
        ema_fast=float(ema3[-1]),
        ema_mid=float(ema8[-1]),
        ema_slow=float(ema21[-1]),
        vwap=vwap_v,
        bb_pct=bb_v,
        adx=adx_v,
        volume_ratio=float(vol_ratio),
        details={"delta_vol": round(dv, 3)},
    )


def compute_weighted_score(signals: dict[str, TFSignal]) -> tuple[str, float]:
    """
    Agrega sinais de múltiplos TFs com pesos.
    Retorna (direction, score_final).
    """
    # 15m e 1h definem tendência real; 1m/3m apenas timing de entrada
    weights = {"1m": 0.05, "3m": 0.10, "5m": 0.25, "15m": 0.35, "1h": 0.25}
    long_w = 0.0
    short_w = 0.0
    total_w = 0.0

    for tf, sig in signals.items():
        w = weights.get(tf, 0.0)
        total_w += w
        if sig.direction == "LONG":
            long_w += sig.score * w
        elif sig.direction == "SHORT":
            short_w += sig.score * w

    if total_w == 0:
        return "NEUTRAL", 0.0

    long_s  = long_w  / total_w
    short_s = short_w / total_w

    if long_s > short_s:
        return "LONG", round(long_s, 1)
    elif short_s > long_s:
        return "SHORT", round(short_s, 1)
    return "NEUTRAL", 0.0
