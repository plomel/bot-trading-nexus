"""
Bot Trading Nexus — Filtro de Regime BTC
Determina se o mercado está em tendência bull/bear com base em EMA50 no 15m.
Usado para bloquear entradas contra a tendência macro.
"""
from __future__ import annotations
import time
import logging
import numpy as np

log = logging.getLogger("nexus.btc_filter")

# Cache do regime para evitar chamadas API constantes
_cache_regime: str = "NEUTRAL"
_cache_ts: float = 0.0
_CACHE_TTL = 300  # actualizar a cada 5 minutos

BTC_SYMBOL = "BTC/USDT:USDT"


def _ema(series: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    result = np.empty_like(series, dtype=float)
    result[0] = series[0]
    for i in range(1, len(series)):
        result[i] = series[i] * k + result[i - 1] * (1 - k)
    return result


async def btc_regime(exchange) -> str:
    """
    Retorna 'BULL', 'BEAR' ou 'NEUTRAL' com base em EMA50 no 15m do BTC.

    - BULL : preço > EMA50 × 1.001  → favorece LONGs, bloqueia SHORTs
    - BEAR : preço < EMA50 × 0.999  → favorece SHORTs, bloqueia LONGs
    - NEUTRAL: dentro da banda → sem bloqueio, exige score mais alto

    Cache de 5 minutos para não sobrecarregar a API.
    """
    global _cache_regime, _cache_ts

    now = time.time()
    if now - _cache_ts < _CACHE_TTL:
        return _cache_regime

    try:
        candles = await exchange.fetch_ohlcv(BTC_SYMBOL, "15m", limit=60)
        if not candles or len(candles) < 55:
            log.warning("btc_filter: candles insuficientes — regime NEUTRAL")
            return "NEUTRAL"

        closes = np.array([c[4] for c in candles], dtype=float)
        ema50 = _ema(closes, 50)
        price = closes[-1]
        e50 = ema50[-1]

        if price > e50 * 1.001:
            regime = "BULL"
        elif price < e50 * 0.999:
            regime = "BEAR"
        else:
            regime = "NEUTRAL"

        _cache_regime = regime
        _cache_ts = now
        log.info(f"BTC regime: {regime} | price=${price:.1f} EMA50=${e50:.1f}")
        return regime

    except Exception as e:
        log.warning(f"btc_filter erro: {e} — regime NEUTRAL")
        return "NEUTRAL"
