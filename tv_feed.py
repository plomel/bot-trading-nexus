"""
Bot Trading Nexus — Wrapper tvDatafeed
Fonte suplementar de sinais do TradingView.
Non-blocking: corre em executor para não bloquear o event loop.
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional

log = logging.getLogger("nexus.tvfeed")

# tvDatafeed é opcional — não impede o bot de correr se não estiver instalado
try:
    from tvDatafeed import TvDatafeed, Interval
    _TV_AVAILABLE = True
except ImportError:
    _TV_AVAILABLE = False
    log.warning("tvDatafeed não instalado — sinal TV desativado. Instala: pip install tvDatafeed")

# Mapeamento TF Nexus → Interval TV
TF_MAP = {
    "1m":  "Interval.in_1_minute",
    "3m":  "Interval.in_3_minute",
    "5m":  "Interval.in_5_minute",
    "15m": "Interval.in_15_minute",
    "1h":  "Interval.in_1_hour",
}

# Cache para evitar throttling (TV tem rate limit)
_cache: dict[str, tuple[float, str]] = {}  # key → (timestamp, direction)
_CACHE_TTL = 60.0  # segundos


def _tv_interval(tf: str):
    if not _TV_AVAILABLE:
        return None
    mapping = {
        "1m":  Interval.in_1_minute,
        "3m":  Interval.in_3_minute,
        "5m":  Interval.in_5_minute,
        "15m": Interval.in_15_minute,
        "1h":  Interval.in_1_hour,
    }
    return mapping.get(tf)


def _get_tv_signal_sync(symbol: str, tf: str, bars: int = 50) -> str:
    """
    Corre em thread separada via run_in_executor.
    Retorna 'LONG', 'SHORT' ou 'NEUTRAL'.
    """
    if not _TV_AVAILABLE:
        return "NEUTRAL"

    cache_key = f"{symbol}_{tf}"
    now = time.time()
    if cache_key in _cache:
        ts, direction = _cache[cache_key]
        if now - ts < _CACHE_TTL:
            return direction

    try:
        tv = TvDatafeed()
        interval = _tv_interval(tf)
        if interval is None:
            return "NEUTRAL"

        # Símbolo Binance Perps no TV: PEPEUSDT → PEPEUSDT.P
        tv_symbol = symbol.replace("USDT", "USDT.P") if not symbol.endswith(".P") else symbol

        df = tv.get_hist(tv_symbol, "BINANCE", interval=interval, n_bars=bars)
        if df is None or len(df) < 20:
            return "NEUTRAL"

        close = df["close"].values
        # EMA 9 vs EMA 21 — sinal simples de confirmação
        def ema_calc(s, p):
            k = 2 / (p + 1)
            result = [s[0]]
            for v in s[1:]:
                result.append(v * k + result[-1] * (1 - k))
            return result

        ema9  = ema_calc(close.tolist(), 9)[-1]
        ema21 = ema_calc(close.tolist(), 21)[-1]
        price = close[-1]

        if price > ema9 > ema21:
            direction = "LONG"
        elif price < ema9 < ema21:
            direction = "SHORT"
        else:
            direction = "NEUTRAL"

        _cache[cache_key] = (now, direction)
        return direction

    except Exception as e:
        log.debug(f"tvDatafeed erro {symbol} {tf}: {e}")
        return "NEUTRAL"


async def get_tv_signal(symbol: str, tf: str = "5m") -> str:
    """
    Versão async — corre em executor para não bloquear o bot.
    Timeout 10s — se demorar mais, retorna NEUTRAL sem esperar.
    """
    if not _TV_AVAILABLE:
        return "NEUTRAL"
    loop = asyncio.get_event_loop()
    try:
        direction = await asyncio.wait_for(
            loop.run_in_executor(None, _get_tv_signal_sync, symbol, tf),
            timeout=10.0
        )
        return direction
    except asyncio.TimeoutError:
        log.debug(f"tvDatafeed timeout {symbol} {tf}")
        return "NEUTRAL"
    except Exception as e:
        log.debug(f"tvDatafeed async erro: {e}")
        return "NEUTRAL"


def tv_available() -> bool:
    return _TV_AVAILABLE
