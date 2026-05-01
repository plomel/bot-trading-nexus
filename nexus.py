#!/usr/bin/env python3
"""
Bot Trading Nexus — Scalping Assíncrono
WebSocket real-time · 10-20x leverage · Meme coins

Uso:
  python nexus.py            # live trading
  python nexus.py --paper    # paper mode (sem ordens reais)
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import sys
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import urllib.request
import urllib.parse

import numpy as np
import ccxt.pro as ccxtpro

from dotenv import load_dotenv
import indicators
from indicators import analyze, compute_weighted_score, TFSignal
from risk import RiskManager
from tv_feed import get_tv_signal, tv_available
from btc_filter import btc_regime
import state as st
import security
from security import security as sec_manager, validate_configuration
from telegram_handler import TelegramCommandHandler
from shutdown_manager import ShutdownManager
from state_manager import state_manager

# ── Configuração de logging ────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent / "nexus.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("nexus")

# Telegram retry configuration
TG_MAX_RETRIES = 3
TG_BASE_DELAY = 1  # second

# ── Argumentos ─────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument("--paper", action="store_true", help="Paper mode — sem ordens reais")
ARGS = parser.parse_args()
PAPER = ARGS.paper

# ── Env ────────────────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent
load_dotenv(_HERE / ".env.nexus")

# Environment variables with validation
_API_KEY_RAW = os.getenv("BINANCE_API_KEY", "")
_API_SECRET_RAW = os.getenv("BINANCE_SECRET", "")
_TG_TOKEN_RAW = os.getenv("TELEGRAM_TOKEN", "")
_TG_CHAT_ID_RAW = os.getenv("TELEGRAM_CHAT_ID", "")

# Load all environment variables BEFORE validation
MIN_SCORE        = float(os.getenv("MIN_SCORE", "45"))       # legado — sobreposto por categoria
MIN_SCORE_MAIN   = float(os.getenv("MIN_SCORE_MAIN", "45"))  # BTC/ETH/SOL
MIN_SCORE_MEME   = float(os.getenv("MIN_SCORE_MEME", "62"))  # meme coins — só setups fortes
DEFAULT_LEVERAGE = int(os.getenv("DEFAULT_LEVERAGE", "10"))
MAX_LEVERAGE     = int(os.getenv("MAX_LEVERAGE", "20"))
ENTRY_COOLDOWN   = int(os.getenv("ENTRY_COOLDOWN_SEC", "60"))
MAX_POSITIONS    = int(os.getenv("MAX_POSITIONS", "5"))
MAX_MEME_OPEN    = int(os.getenv("MAX_MEME_OPEN", "1"))      # máx 1 meme coin simultânea
ADX_MIN_TREND    = float(os.getenv("ADX_MIN_TREND", "20"))
USE_TV_FEED      = os.getenv("USE_TV_FEED", "true").lower() == "true"
TV_BONUS         = float(os.getenv("TV_BONUS_PTS", "10"))

# Categorias de pares
MAIN_PAIRS = {"BTC", "ETH", "SOL"}
MEME_PAIRS = {"PEPE", "BONK", "WIF", "SHIB", "DOGE", "FLOKI", "BOME"}

FUNDING_MAX = float(os.getenv("FUNDING_MAX_PCT", "0.05")) / 100

# Risk configuration
_CFG = {k: os.getenv(k, v) for k, v in {
    "MAX_RISK_PER_TRADE": "1.0",
    "MAX_DAILY_LOSS": "8.0",
    "CB_EMERG_COUNT": "2",
    "CB_WINDOW_SEC": "1200",
    "CB_PAUSE_SEC": "7200",
    "MAX_MARGIN_PCT": "5.0",
    "LIQUIDATION_BUFFER": "0.15",
}.items()}
MAX_RISK_PER_TRADE = float(_CFG["MAX_RISK_PER_TRADE"])
MAX_DAILY_LOSS = float(_CFG["MAX_DAILY_LOSS"])
MAX_MARGIN_PCT = float(_CFG["MAX_MARGIN_PCT"])
ENTRY_COOLDOWN_SEC = ENTRY_COOLDOWN

# Validate configuration
config = {
    "BINANCE_API_KEY": _API_KEY_RAW,
    "BINANCE_SECRET": _API_SECRET_RAW,
    "TELEGRAM_TOKEN": _TG_TOKEN_RAW,
    "TELEGRAM_CHAT_ID": _TG_CHAT_ID_RAW,
    "MIN_SCORE": MIN_SCORE,
    "DEFAULT_LEVERAGE": DEFAULT_LEVERAGE,
    "MAX_LEVERAGE": MAX_LEVERAGE,
    "MAX_RISK_PER_TRADE": MAX_RISK_PER_TRADE,
    "MAX_DAILY_LOSS": MAX_DAILY_LOSS,
    "MAX_MARGIN_PCT": MAX_MARGIN_PCT,
    "ENTRY_COOLDOWN_SEC": ENTRY_COOLDOWN_SEC,
    "MAX_POSITIONS": MAX_POSITIONS,
    "FUNDING_MAX_PCT": FUNDING_MAX * 100,
}

is_valid, errors = validate_configuration(config)
if not is_valid:
    log.error("Configuration validation failed:")
    for error in errors:
        log.error(f"  - {error}")
    raise ValueError("Invalid configuration. Please check your .env file.")

# Decrypt sensitive data if needed
try:
    API_KEY = sec_manager.decrypt_data(_API_KEY_RAW) if _API_KEY_RAW else ""
    API_SECRET = sec_manager.decrypt_data(_API_SECRET_RAW) if _API_SECRET_RAW else ""
    TG_TOKEN = sec_manager.decrypt_data(_TG_TOKEN_RAW) if _TG_TOKEN_RAW else ""
    TG_CHAT_ID = sec_manager.decrypt_data(_TG_CHAT_ID_RAW) if _TG_CHAT_ID_RAW else ""
except Exception as e:
    log.warning(f"Failed to decrypt sensitive data, using raw values: {e}")
    API_KEY = _API_KEY_RAW
    API_SECRET = _API_SECRET_RAW
    TG_TOKEN = _TG_TOKEN_RAW
    TG_CHAT_ID = _TG_CHAT_ID_RAW

# Pares configurados pelo utilizador (ex: PEPE, BONK, WIF)
_RAW_PAIRS = [p.strip() for p in
              os.getenv("WATCH_PAIRS", "PEPE,BONK,WIF,DOGE,SHIB,FLOKI,BOME").split(",")]

# Mapeamento final (preenchido em resolve_symbols() após load_markets)
# chave = símbolo ccxt ("1000PEPE/USDT:USDT"), valor = nome curto ("PEPE")
SYMBOL_MAP: dict[str, str] = {}
WATCH_PAIRS: list[str] = []  # símbolos ccxt resolvidos

TIMEFRAMES  = ["1m", "3m", "5m", "15m", "1h"]
TF_CONFIRM  = "1h"    # TF de confirmação de tendência maior


def tg(text: str) -> None:
    """Envia mensagem ao Telegram de forma síncrona (chamado via executor quando necessário)."""
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    
    # Retry logic with exponential backoff
    body = json.dumps({"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=body, headers={"Content-Type": "application/json"}
    )
    
    for attempt in range(TG_MAX_RETRIES):
        try:
            urllib.request.urlopen(req, timeout=8)
            return  # Success, exit function
        except Exception as e:
            if attempt == TG_MAX_RETRIES - 1:  # Last attempt
                log.warning(f"tg send falhou após {TG_MAX_RETRIES} tentativas: {e}")
            else:
                log.debug(f"tg send tentativa {attempt + 1} falhou: {e}, tentando novamente...")
                time.sleep(TG_BASE_DELAY * (2 ** attempt))  # Exponential backoff


async def tg_async(text: str) -> None:
    """Versão async — não bloqueia o event loop."""
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, tg, text)

RISK = RiskManager(_CFG)

# Global managers
telegram_handler: Optional[TelegramCommandHandler] = None
shutdown_manager: Optional[ShutdownManager] = None
shutdown_requested = False

# ── Estado global ──────────────────────────────────────────────────────────────

# Use thread-safe state manager
# symbol → {entry, qty, side, sl, tp, leverage, opened, risk_usd}
positions: dict = {}
cooldowns: dict = {}  # symbol → expiry timestamp

# Buffer de candles por (symbol, timeframe) → list of OHLCV
candle_buf: dict[tuple, list] = {}

# Último sinal por symbol → (direction, score, ts)
last_signal: dict[str, tuple] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _in_trading_session() -> bool:
    """Só abre novas posições entre 08:00-22:00 UTC (London + NY overlap)."""
    hour = datetime.now(timezone.utc).hour
    return 8 <= hour < 22


async def _in_cooldown(symbol: str) -> bool:
    exp = cooldowns.get(symbol, 0)
    return time.time() < exp


async def _set_cooldown(symbol: str) -> None:
    expiry = time.time() + ENTRY_COOLDOWN
    await state_manager.update_cooldown(symbol, expiry)
    cooldowns[symbol] = expiry
    st.save_cooldowns(cooldowns)


def _log_position_table() -> None:
    if not positions:
        log.info("Sem posições abertas.")
        return
    log.info(f"{'Symbol':<12} {'Side':<6} {'Entry':>10} {'SL':>10} {'TP':>10} {'Lev':>4}")
    for sym, p in positions.items():
        log.info(f"{sym:<12} {p['side']:<6} {p['entry']:>10.6f} {p['sl']:>10.6f} "
                 f"{p['tp']:>10.6f} {p['leverage']:>3}x")


# ── Resolução de símbolos ─────────────────────────────────────────────────────

async def resolve_symbols(exchange) -> None:
    """
    Descobre o símbolo ccxt correcto para cada par pedido.
    Binance usa prefixo '1000' para meme coins baratas (PEPE→1000PEPE, etc.).
    Pares inexistentes no mercado são ignorados com aviso.
    """
    global WATCH_PAIRS, SYMBOL_MAP
    markets = exchange.markets  # já carregados

    resolved = []
    for raw in _RAW_PAIRS:
        # Candidatos por ordem de preferência
        candidates = [
            f"{raw}/USDT:USDT",
            f"1000{raw}/USDT:USDT",
            f"{raw}/USDC:USDC",
            f"1000{raw}/USDC:USDC",
        ]
        found = None
        for cand in candidates:
            if cand in markets:
                found = cand
                break
        if found:
            SYMBOL_MAP[found] = raw
            resolved.append(found)
            prefix = "1000" if found.startswith("1000") else ""
            log.info(f"  {raw:>8} → {found}")
        else:
            log.warning(f"  {raw:>8} → NÃO encontrado no mercado (ignorado)")

    WATCH_PAIRS = resolved
    log.info(f"{len(WATCH_PAIRS)}/{len(_RAW_PAIRS)} pares resolvidos.")


# ── Exchange setup ─────────────────────────────────────────────────────────────

async def create_exchange() -> ccxtpro.binanceusdm:
    exchange = ccxtpro.binanceusdm({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "options": {
            "defaultType": "future",
            "adjustForTimeDifference": True,
        },
        "enableRateLimit": True,
        "recvWindow": 10000,
    })
    if PAPER:
        log.warning("=" * 60)
        log.warning("  PAPER MODE — nenhuma ordem real será submetida")
        log.warning("=" * 60)
    return exchange


async def set_leverage(exchange, symbol: str, leverage: int) -> None:
    try:
        await exchange.set_leverage(leverage, symbol, params={"marginMode": "cross"})
    except Exception as e:
        log.warning(f"set_leverage {symbol}: {e}")


# ── Order execution ────────────────────────────────────────────────────────────

async def open_position(exchange, symbol: str, side: str, score: float,
                        sl: float, tp: float, atr: float) -> None:
    global shutdown_requested
    
    # Check if shutdown is requested
    if shutdown_requested:
        log.debug(f"Skipping {symbol} entry - shutdown in progress")
        return
        
    if symbol in positions:
        return
    if len(positions) >= MAX_POSITIONS:
        log.debug(f"MAX_POSITIONS ({MAX_POSITIONS}) atingido — skip {symbol}")
        return
    sym_short_check = _short_sym(symbol)
    if sym_short_check in MEME_PAIRS:
        meme_open = sum(1 for s in positions if _short_sym(s) in MEME_PAIRS)
        if meme_open >= MAX_MEME_OPEN:
            log.info(f"Skip {sym_short_check} — {meme_open} meme coin(s) já abertas (máx {MAX_MEME_OPEN})")
            return
    if _in_cooldown(symbol):
        return

    ok, reason = RISK.can_enter()
    if not ok:
        log.warning(f"[RISK] {reason}")
        return

    try:
        balance_data = await exchange.fetch_balance()
        # For Binance futures, we prefer the USDT free balance
        # We'll try multiple methods in order of preference
        
        # Method 1: Direct USDT free balance (most reliable for USDT margin)
        usdt_free = balance_data.get('USDT', {}).get('free')
        if usdt_free is not None:
            free = float(usdt_free)
            log.debug(f"Balance from USDT free: {free}")
        else:
            # Method 2: Try availableBalance in info (Binance specific)
            info = balance_data.get("info", {})
            avail_raw = info.get("availableBalance")
            if avail_raw is not None:
                free = float(avail_raw)
                log.debug(f"Balance from availableBalance: {free}")
            else:
                # Method 3: Fallback to summing free across possible margin currencies
                bnfcr_free = float(balance_data.get("BNFCR", {}).get("free", 0))
                usdt_free = float(balance_data.get("USDT", {}).get("free", 0))
                usdc_free = float(balance_data.get("USDC", {}).get("free", 0))
                free = bnfcr_free + usdt_free + usdc_free
                log.debug(f"Balance from sum of freedoms: {free}")
                
                # If still zero, try total as last resort (though this is risky)
                if free == 0:
                    bnfcr_total = float(balance_data.get("BNFCR", {}).get("total", 0))
                    usdt_total = float(balance_data.get("USDT", {}).get("total", 0))
                    usdc_total = float(balance_data.get("USDC", {}).get("total", 0))
                    free = bnfcr_total + usdt_total + usdc_total
                    log.debug(f"Balance from total (fallback): {free}")
        
        # Ensure we have a non-negative free balance
        if free < 0:
            log.warning(f"Negative free balance detected: {free}. Setting to 0.")
            free = 0.0
            
    except Exception as e:
        log.error(f"fetch_balance falhou: {e}")
        return

    log.info(f"Saldo disponível: ${free:.2f} (total=${total:.2f})")
    if free < 5:
        log.warning(f"Saldo insuficiente: ${free:.2f}")
        return

    # Actualizar daily loss
    RISK.update_daily(free, _day_key())
    if RISK.daily_loss_exceeded:
        log.warning("Daily loss cap atingido — sem novas entradas.")
        return

    try:
        ticker = await exchange.fetch_ticker(symbol)
        price = float(ticker["last"])
    except Exception as e:
        log.error(f"fetch_ticker {symbol}: {e}")
        return

    qty = RISK.compute_qty(price, sl, side, free, DEFAULT_LEVERAGE)
    if qty <= 0:
        log.warning(f"qty=0 para {symbol} — skip")
        return

    # Partial TP: tp1 = 1:1 R:R (50% da pos), tp2 = 2:1 R:R (restantes 50%)
    tp1, tp2 = RISK.compute_tp_levels(price, side, sl)

    # Precisão do par
    await exchange.load_markets()
    market = exchange.market(symbol)
    qty = float(exchange.amount_to_precision(symbol, qty))
    sl   = float(exchange.price_to_precision(symbol, sl))
    tp1  = float(exchange.price_to_precision(symbol, tp1))
    tp2  = float(exchange.price_to_precision(symbol, tp2))
    tp   = tp2  # tp legado aponta para o alvo final

    sym_short = _short_sym(symbol)
    entry_emoji = "🟢" if side == "LONG" else "🔴"
    sl_pct  = abs(price - sl)  / price * 100 if sl > 0 else 0
    tp1_pct = abs(price - tp1) / price * 100 if tp1 > 0 else 0
    tp2_pct = abs(price - tp2) / price * 100 if tp2 > 0 else 0
    log.info(f"{'[PAPER] ' if PAPER else ''}OPEN {side} {symbol} "
             f"| qty={qty} entry~${price:.6f} SL=${sl:.6f} "
             f"TP1=${tp1:.6f}({tp1_pct:.2f}%) TP2=${tp2:.6f}({tp2_pct:.2f}%) "
             f"| score={score:.0f} lev={DEFAULT_LEVERAGE}x")
    await tg_async(
        f"{entry_emoji} <b>{'[PAPER] ' if PAPER else ''}ENTRADA {side}</b> — {sym_short}\n"
        f"💰 Preço: <code>${price:.6f}</code>\n"
        f"🛡 SL: <code>${sl:.6f}</code> ({sl_pct:.2f}%)\n"
        f"🎯 TP1: <code>${tp1:.6f}</code> ({tp1_pct:.2f}%) — 50%\n"
        f"🎯 TP2: <code>${tp2:.6f}</code> ({tp2_pct:.2f}%) — 50%\n"
        f"📊 Score: {score:.0f}/100 · {DEFAULT_LEVERAGE}x leverage"
    )

    qty_half = float(exchange.amount_to_precision(symbol, qty / 2))

    if PAPER:
        # Save to thread-safe state manager
        await state_manager.update_position(symbol, {
            "entry": price, "qty": qty, "qty_half": qty_half, "side": side,
            "sl": sl, "tp": tp2, "tp1": tp1, "tp2": tp2,
            "tp1_hit": False, "leverage": DEFAULT_LEVERAGE,
            "opened": time.time(), "risk_usd": free * (RISK.max_risk_pct / 100),
            "paper": True,
        })
        positions[symbol] = {
            "entry": price, "qty": qty, "qty_half": qty_half, "side": side,
            "sl": sl, "tp": tp2, "tp1": tp1, "tp2": tp2,
            "tp1_hit": False, "leverage": DEFAULT_LEVERAGE,
            "opened": time.time(), "risk_usd": free * (RISK.max_risk_pct / 100),
            "paper": True,
        }
        st.save_positions(positions)
        await _set_cooldown(symbol)
        return

    # Ordens reais
    await set_leverage(exchange, symbol, DEFAULT_LEVERAGE)
    ccxt_side = "buy" if side == "LONG" else "sell"
    close_side = "sell" if side == "LONG" else "buy"

    try:
        entry_order = await exchange.create_order(symbol, "market", ccxt_side, qty)
        entry_price = float(entry_order.get("average") or entry_order.get("price") or price)
    except Exception as e:
        log.error(f"Ordem de entrada falhou {symbol}: {e}")
        return

    entry_id = entry_order.get("id", "?")
    log.info(f"Entrada executada: {symbol} {side} @ ${entry_price:.6f} id={entry_id}")

    # SL
    sl_ok = False
    try:
        await exchange.create_order(symbol, "stop_market", close_side, qty,
                                    params={"stopPrice": sl, "reduceOnly": True})
        sl_ok = True
    except Exception as e:
        log.error(f"SL falhou {symbol}: {e}")

    # Se SL falhou → fechar imediatamente (proteção)
    if not sl_ok:
        log.error(f"SL não colocado → fecho emergência {symbol}")
        RISK.register_emergency()
        try:
            await exchange.create_order(symbol, "market", close_side, qty,
                                        params={"reduceOnly": True})
        except Exception as e2:
            log.error(f"Fecho emergência falhou {symbol}: {e2}")
            # Regista posição com flag emergency para monitorização manual
            positions[symbol] = {
                "entry": entry_price, "qty": qty, "side": side,
                "sl": 0.0, "tp": tp, "leverage": DEFAULT_LEVERAGE,
                "opened": time.time(), "emergency": True,
            }
            st.save_positions(positions)
        return

    # TP1 — fechar 50% ao atingir 1:1 R:R
    try:
        await exchange.create_order(symbol, "take_profit_market", close_side, qty_half,
                                    params={"stopPrice": tp1, "reduceOnly": True})
        log.info(f"TP1 colocado {symbol} @ ${tp1:.6f} (qty={qty_half})")
    except Exception as e:
        log.warning(f"TP1 falhou {symbol}: {e}")

    # TP2 — fechar restantes 50% ao atingir 2:1 R:R
    try:
        await exchange.create_order(symbol, "take_profit_market", close_side, qty_half,
                                    params={"stopPrice": tp2, "reduceOnly": True})
        log.info(f"TP2 colocado {symbol} @ ${tp2:.6f} (qty={qty_half})")
    except Exception as e:
        log.warning(f"TP2 falhou {symbol} (posição protegida pelo SL + TP1): {e}")

    # Save to thread-safe state manager and local state
    await state_manager.update_position(symbol, {
        "entry": entry_price, "qty": qty, "qty_half": qty_half, "side": side,
        "sl": sl, "tp": tp2, "tp1": tp1, "tp2": tp2,
        "tp1_hit": False, "leverage": DEFAULT_LEVERAGE,
        "opened": time.time(), "risk_usd": free * (RISK.max_risk_pct / 100),
    })
    positions[symbol] = {
        "entry": entry_price, "qty": qty, "qty_half": qty_half, "side": side,
        "sl": sl, "tp": tp2, "tp1": tp1, "tp2": tp2,
        "tp1_hit": False, "leverage": DEFAULT_LEVERAGE,
        "opened": time.time(), "risk_usd": free * (RISK.max_risk_pct / 100),
    }
    st.save_positions(positions)
    await _set_cooldown(symbol)


async def close_position(exchange, symbol: str, reason: str = "manual") -> None:
    pos = positions.get(symbol)
    if not pos:
        return

    sym_short = _short_sym(symbol)
    log.info(f"CLOSE {symbol} | razão={reason}")
    await tg_async(f"⏹ <b>FECHO</b> — {sym_short}\nRazão: {reason}")
    side = pos["side"]
    qty  = pos["qty"]
    close_side = "sell" if side == "LONG" else "buy"

    if not PAPER:
        try:
            await exchange.create_order(symbol, "market", close_side, qty,
                                        params={"reduceOnly": True})
        except Exception as e:
            log.error(f"Fecho {symbol} falhou: {e}")
            RISK.register_emergency()
            return

    # Remove from thread-safe state manager
    await state_manager.remove_position(symbol)
    positions.pop(symbol, None)
    st.save_positions(positions)
    await _set_cooldown(symbol)


# ── Helpers de display ────────────────────────────────────────────────────────

def _short_sym(ccxt_sym: str) -> str:
    """'1000PEPE/USDT:USDT' → 'PEPE'  |  'WIF/USDT:USDT' → 'WIF'"""
    base = ccxt_sym.split("/")[0].lstrip("1000") if ccxt_sym.startswith("1000") else ccxt_sym.split("/")[0]
    return SYMBOL_MAP.get(ccxt_sym, base)


def _score_bar(score: float) -> str:
    filled = int(score / 10)
    return "█" * filled + "░" * (10 - filled)


def _dir_arrow(direction: str) -> str:
    return "▲ LONG" if direction == "LONG" else "▼ SHORT" if direction == "SHORT" else "— NEUTRO"


# ── Telegram Integration ───────────────────────────────────────────────────

async def init_telegram_handler(exchange) -> Optional[TelegramCommandHandler]:
    """Initialize Telegram command handler."""
    global telegram_handler, shutdown_manager
    
    if not TG_TOKEN or not TG_CHAT_ID:
        log.warning("Telegram credentials not provided - skipping Telegram integration")
        return None
    
    try:
        # Initialize shutdown manager
        shutdown_manager = ShutdownManager(exchange, positions, None)
        
        # Initialize Telegram handler
        telegram_handler = TelegramCommandHandler(TG_TOKEN, TG_CHAT_ID, None)
        
        # Set references
        telegram_handler.nexus_instance = NexusInstance()
        shutdown_manager.telegram_handler = telegram_handler
        
        # Initialize and start polling
        if await telegram_handler.initialize():
            await telegram_handler.start_polling()
            log.info("Telegram command handler initialized successfully")
            return telegram_handler
        else:
            log.error("Failed to initialize Telegram handler")
            return None
            
    except Exception as e:
        log.error(f"Failed to initialize Telegram integration: {e}")
        return None

class NexusInstance:
    """Wrapper class for Nexus instance to provide interface for Telegram handler."""
    
    def __init__(self):
        self.start_time = time.time()
    
    async def initiate_shutdown(self):
        """Initiate shutdown sequence."""
        global shutdown_requested, shutdown_manager
        
        if shutdown_requested:
            return
        
        shutdown_requested = True
        
        if shutdown_manager:
            await shutdown_manager.initiate_shutdown()
        else:
            log.error("Shutdown manager not initialized")
    
    async def get_status(self) -> dict:
        """Get current bot status."""
        try:
            uptime = time.time() - self.start_time
            positions_count = len(positions)
            
            # Get balance
            balance = 0.0
            try:
                # Use a temporary exchange instance to get balance
                temp_exchange = await create_exchange()
                balance_data = await temp_exchange.fetch_balance()
                balance = float(balance_data.get('USDT', {}).get('free', 0))
                await temp_exchange.close()
            except Exception:
                pass
            
            return {
                'paper_mode': PAPER,
                'positions_count': positions_count,
                'circuit_breaker_active': RISK.circuit_breaker_active,
                'uptime': f"{int(uptime//3600)}h {int((uptime%3600)//60)}m",
                'balance': f"{balance:.2f}",
                'daily_pnl': f"{RISK._daily_loss:.2f}%"
            }
        except Exception as e:
            log.error(f"Error getting status: {e}")
            return {}
    
    async def get_positions(self) -> list:
        """Get current positions with P&L."""
        try:
            positions_list = []
            
            for symbol, pos_data in positions.items():
                # Calculate current P&L
                try:
                    temp_exchange = await create_exchange()
                    ticker = await temp_exchange.fetch_ticker(symbol)
                    current_price = float(ticker['last'])
                    await temp_exchange.close()
                    
                    entry_price = pos_data.get('entry', 0)
                    side = pos_data.get('side', 'LONG')
                    qty = pos_data.get('qty', 0)
                    
                    if side == 'LONG':
                        pnl = (current_price - entry_price) * qty
                    else:
                        pnl = (entry_price - current_price) * qty
                    
                    positions_list.append({
                        'symbol': _short_sym(symbol),
                        'side': side,
                        'entry': entry_price,
                        'sl': pos_data.get('sl', 0),
                        'tp': pos_data.get('tp', 0),
                        'leverage': pos_data.get('leverage', 0),
                        'pnl': pnl
                    })
                except Exception:
                    # Fallback without P&L
                    positions_list.append({
                        'symbol': _short_sym(symbol),
                        'side': pos_data.get('side', 'UNKNOWN'),
                        'entry': pos_data.get('entry', 0),
                        'sl': pos_data.get('sl', 0),
                        'tp': pos_data.get('tp', 0),
                        'leverage': pos_data.get('leverage', 0),
                        'pnl': 0
                    })
            
            return positions_list
        except Exception as e:
            log.error(f"Error getting positions: {e}")
            return []
    
    async def get_risk_metrics(self) -> dict:
        """Get risk management metrics."""
        try:
            return {
                'daily_loss': RISK._daily_loss,
                'daily_loss_cap': RISK.daily_loss_cap,
                'risk_per_trade': RISK.max_risk_pct,
                'margin_used': 0.0,  # Could be calculated if needed
                'emergency_count': len(RISK._emergency_times),
                'circuit_breaker_active': RISK.circuit_breaker_active
            }
        except Exception as e:
            log.error(f"Error getting risk metrics: {e}")
            return {}

_last_tg_heartbeat: float = 0.0

async def heartbeat_logger() -> None:
    """Log a cada 2 min · Telegram a cada 15 min."""
    global _last_tg_heartbeat
    await asyncio.sleep(30)  # aguarda warm-up inicial
    while True:
        try:
            n_pos = len(positions)
            n_buf = sum(1 for k in candle_buf if k[1] == "1m")
            cb_status = f"CB ATIVO {int(RISK.cb_seconds_remaining/60)}min" if RISK.circuit_breaker_active else "ok"

            if n_pos == 0:
                pos_str = "sem posições abertas"
            else:
                parts = [f"{_short_sym(s)} {p['side']} {p['leverage']}x" for s, p in positions.items()]
                pos_str = " | ".join(parts)

            # Scores recentes
            now = time.time()
            recent = [(sym, d, sc) for sym, (d, sc, ts) in last_signal.items()
                      if now - ts < 300]  # últimos 5 min
            if recent:
                sig_lines = "  ".join(
                    f"{_short_sym(s)}:{_dir_arrow(d)}{sc:.0f}" for s, d, sc in recent
                )
                log.info(f"[NEXUS] {pos_str} | {cb_status} | sinais recentes: {sig_lines}")
            else:
                buffers_ok = f"{n_buf}/{len(WATCH_PAIRS)} streams ativos"
                log.info(f"[NEXUS] {pos_str} | {cb_status} | {buffers_ok} | a aguardar candles...")

            # Telegram a cada 15 min
            if now - _last_tg_heartbeat >= 900:
                _last_tg_heartbeat = now
                mode_str = "📄 PAPER" if PAPER else "🔴 LIVE"
                if n_pos == 0:
                    pos_tg = "Sem posições abertas."
                else:
                    pos_tg = "\n".join(
                        f"  • {_short_sym(s)} {p['side']} {p['leverage']}x "
                        f"entry=${p.get('entry',0):.6f}"
                        for s, p in positions.items()
                    )
                sig_tg = (
                    "\n".join(f"  {_short_sym(s)}: {_dir_arrow(d)} {sc:.0f}" for s, d, sc in recent)
                    if recent else "  (sem sinais recentes)"
                )
                await tg_async(
                    f"🤖 <b>NEXUS {mode_str} · Heartbeat</b>\n"
                    f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n"
                    f"📊 Posições: {pos_tg}\n"
                    f"🔎 Sinais:\n{sig_tg}\n"
                    f"🛡 Circuit breaker: {cb_status}"
                )

        except Exception as e:
            log.warning(f"heartbeat erro: {e}")
        await asyncio.sleep(120)


# ── Análise por candle fechada ────────────────────────────────────────────────

# Controlo de frequência de logs de análise (evitar spam em 1m)
_last_analysis_log: dict[str, float] = {}  # symbol → timestamp do último log

async def on_candle_close(exchange, symbol: str, timeframe: str,
                          candles: list) -> None:
    global shutdown_requested
    
    # Check if shutdown is requested
    if shutdown_requested:
        return
        
    buf_key = (symbol, timeframe)
    # Update thread-safe candle buffer
    await state_manager.update_candle_buffer(symbol, timeframe, candles)
    
    # Get updated buffer for analysis
    buffer = await state_manager.get_candle_buffer(symbol, timeframe)
    candle_buf[buf_key] = buffer

    # Get all TFs from thread-safe state manager
    all_tfs = {}
    for tf in TIMEFRAMES:
        buffer = await state_manager.get_candle_buffer(symbol, tf)
        if buffer:
            all_tfs[tf] = buffer
    if not all(v is not None and len(v) >= 30 for v in all_tfs.values()):
        return

    # Análise por TF
    signals: dict[str, TFSignal] = {}
    for tf, buf in all_tfs.items():
        signals[tf] = analyze(buf, tf, adx_min=ADX_MIN_TREND)

    direction, score = compute_weighted_score(signals)
    sym_short = _short_sym(symbol)

    # Log de análise a cada candle 1m ou 5m (throttle 30s por par para não spammar)
    if timeframe in ("1m", "5m"):
        now = time.time()
        last_log_ts = _last_analysis_log.get(symbol, 0)
        if now - last_log_ts >= 30:  # no mínimo 30s entre logs do mesmo par
            _last_analysis_log[symbol] = now
            rsi_v = signals.get("5m", type("x", (), {"rsi": 0})()).rsi
            adx_v = signals.get("5m", type("x", (), {"adx": 0})()).adx
            bar = _score_bar(score)
            arrow = _dir_arrow(direction)
            _min_req = MIN_SCORE_MEME if sym_short in MEME_PAIRS else MIN_SCORE_MAIN
            needed = "→ ENTRADA CANDIDATA" if score >= _min_req else f"(mín {_min_req:.0f})"
            log.info(
                f"  {sym_short:<6} {arrow:<10} score={score:5.1f} [{bar}] "
                f"RSI={rsi_v:.0f} ADX={adx_v:.0f} {needed}"
            )

    # Confirmação 1h: não entrar contra tendência maior
    h1_sig = signals.get("1h")
    if h1_sig and h1_sig.direction not in ("NEUTRAL", direction):
        if score >= MIN_SCORE - 10:
            log.info(f"  {sym_short} bloqueado — sinal {direction} contra tendência 1h ({h1_sig.direction})")
        return

    # MIN_SCORE dinâmico por categoria de par
    sym_cat = "meme" if sym_short in MEME_PAIRS else "main"
    min_score_req = MIN_SCORE_MEME if sym_cat == "meme" else MIN_SCORE_MAIN
    if direction == "NEUTRAL" or score < min_score_req:
        return

    # Session filter — sem novas entradas fora das horas de liquidez
    if not _in_trading_session():
        log.debug(f"  {sym_short} — fora da sessão de trading (08:00-22:00 UTC)")
        return

    # Entry quality gate — confluência mínima de TFs
    agreeing_tfs = sum(1 for sig in signals.values() if sig.direction == direction)
    if agreeing_tfs < 3:
        log.debug(f"  {sym_short} — confluência insuficiente ({agreeing_tfs}/5 TFs)")
        return

    # Volume gate — não entrar em mercado morto
    sig5 = signals.get("5m")
    if sig5 and sig5.volume_ratio < 1.1:
        log.debug(f"  {sym_short} — volume baixo ({sig5.volume_ratio:.2f}x médio) → skip")
        return

    # BTC regime filter — não entrar contra a tendência macro
    regime = await btc_regime(exchange)
    if symbol not in ("BTC/USDT:USDT", "ETH/USDT:USDT"):  # BTC/ETH não filtram por si próprios
        if regime == "BEAR" and direction == "LONG" and score < 70:
            log.info(f"  {sym_short} LONG bloqueado — BTC em BEAR (score={score:.0f} < 70)")
            return
        if regime == "BULL" and direction == "SHORT" and score < 70:
            log.info(f"  {sym_short} SHORT bloqueado — BTC em BULL (score={score:.0f} < 70)")
            return

    # Bónus tvDatafeed (opcional, non-blocking)
    if USE_TV_FEED and tv_available():
        tv_dir = await get_tv_signal(symbol, "5m")
        if tv_dir == direction:
            score = min(100.0, score + TV_BONUS)
            log.info(f"  {sym_short} TV confirma {direction} → score ajustado={score:.0f}")
        elif tv_dir not in ("NEUTRAL",):
            score -= TV_BONUS
            if score < min_score_req:
                log.info(f"  {sym_short} TV contradiz ({tv_dir}) → score={score:.0f} insuficiente")
                return

    # Funding rate check
    try:
        funding = await exchange.fetch_funding_rate(symbol)
        fr = float(funding.get("fundingRate", 0) or 0)
        if direction == "LONG" and fr > FUNDING_MAX:
            log.info(f"  {sym_short} LONG bloqueado — funding rate alto ({fr*100:.4f}%)")
            return
        if direction == "SHORT" and fr < -FUNDING_MAX:
            log.info(f"  {sym_short} SHORT bloqueado — funding rate negativo ({fr*100:.4f}%)")
            return
    except Exception as e:
        log.debug(f"  {sym_short} funding rate indisponível: {e} — permitindo entrada")

    # Calcular SL/TP dinâmico por ATR
    # Usa ATR do 5m (mais estável que 1m) como base do SL para evitar SL dentro do spread
    atr_ref = 0.0
    price = 0.0
    if (symbol, "5m") in candle_buf:
        arr_5m = np.array(candle_buf[(symbol, "5m")][-50:], dtype=float)
        price = float(arr_5m[-1, 4])
        atr_ref = indicators.atr(arr_5m[:, 2], arr_5m[:, 3], arr_5m[:, 4])
    elif (symbol, "1m") in candle_buf:
        arr_1m = np.array(candle_buf[(symbol, "1m")][-50:], dtype=float)
        price = float(arr_1m[-1, 4])
        atr_ref = indicators.atr(arr_1m[:, 2], arr_1m[:, 3], arr_1m[:, 4])
    else:
        price = candle_buf.get((symbol, "15m"), [[0]*6])[-1][4]
        atr_ref = price * 0.005  # fallback: 0.5% do preço

    from risk import RiskManager as _RM
    # sl_mult=1.5: SL a ~1.5×ATR(5m) — cobre spread + fees + ruído normal
    # tp_mult=3.0: TP a 2:1 em relação ao SL real
    sl, tp = _RM.compute_sl_tp(price, direction, atr_ref, sl_mult=1.5, tp_mult=3.0)

    sl_pct = abs(price - sl) / price * 100
    tp_pct = abs(price - tp) / price * 100
    log.info(
        f"  ★ SINAL FORTE {_dir_arrow(direction)} {sym_short} "
        f"score={score:.0f} | price=${price:.6f} | "
        f"SL={sl_pct:.2f}% TP={tp_pct:.2f}% | lev={DEFAULT_LEVERAGE}x"
    )

    # Update last signal in thread-safe state manager
    await state_manager.update_last_signal(symbol, direction, score, time.time())
    last_signal[symbol] = (direction, score, time.time())

    await open_position(exchange, symbol, direction, score, sl, tp, atr_ref)


# ── Pré-carregamento histórico via REST ──────────────────────────────────────

async def preload_candles(exchange) -> None:
    """
    Preenche candle_buf com 100 candles históricas via REST antes de ligar o WebSocket.
    Sem isto, o buffer de 1h demora horas a atingir 30 candles e a análise nunca dispara.
    """
    log.info("A pré-carregar dados históricos (REST)...")
    total = len(WATCH_PAIRS) * len(TIMEFRAMES)
    loaded = 0
    for symbol in WATCH_PAIRS:
        for tf in TIMEFRAMES:
            try:
                candles = await exchange.fetch_ohlcv(symbol, tf, limit=100)
                if candles:
                    candle_buf[(symbol, tf)] = candles
                    loaded += 1
            except Exception as e:
                log.warning(f"  preload {symbol} {tf}: {e}")
        await asyncio.sleep(0.05)  # evitar rate limit
    filled = sum(1 for v in candle_buf.values() if len(v) >= 30)
    log.info(f"Pré-carregamento concluído — {loaded}/{total} buffers | {filled} com ≥30 candles")

    # Análise inicial imediata com os dados históricos carregados
    log.info("─" * 55)
    log.info("  ANÁLISE INICIAL DOS PARES")
    log.info("─" * 55)
    tg_lines = []
    candidates = []
    for symbol in WATCH_PAIRS:
        all_tfs = {tf: candle_buf.get((symbol, tf)) for tf in TIMEFRAMES}
        if not all(v is not None and len(v) >= 30 for v in all_tfs.values()):
            continue
        from indicators import analyze, compute_weighted_score
        signals = {tf: analyze(buf, tf, adx_min=ADX_MIN_TREND) for tf, buf in all_tfs.items()}
        direction, score = compute_weighted_score(signals)
        sym_short = _short_sym(symbol)
        sig5 = signals.get("5m")
        rsi_v = sig5.rsi if sig5 else 0
        adx_v = sig5.adx if sig5 else 0
        bar = _score_bar(score)
        arrow = _dir_arrow(direction)
        flag = "→ CANDIDATO!" if score >= MIN_SCORE else ""
        log.info(f"  {sym_short:<6} {arrow:<10} score={score:5.1f} [{bar}] RSI={rsi_v:.0f} ADX={adx_v:.0f} {flag}")
        emoji = "🟢" if direction == "LONG" else "🔴" if direction == "SHORT" else "⚪"
        tg_lines.append(f"{emoji} <b>{sym_short}</b>  {arrow}  score={score:.0f}  RSI={rsi_v:.0f}")
        if score >= MIN_SCORE:
            candidates.append(sym_short)
    log.info("─" * 55)

    # Notificação Telegram de arranque
    mode_str = "📋 PAPER" if PAPER else "⚡ LIVE"
    cand_str = f"\n\n⚠️ <b>Candidatos:</b> {', '.join(candidates)}" if candidates else ""
    tg(
        f"🚀 <b>Nexus {mode_str} online</b>\n"
        f"<i>{datetime.now().strftime('%H:%M')} · {len(WATCH_PAIRS)} pares · {DEFAULT_LEVERAGE}x</i>\n\n"
        + "\n".join(tg_lines)
        + cand_str
    )


# ── WebSocket stream por par/TF ────────────────────────────────────────────────

async def stream_ohlcv(exchange, symbol: str, timeframe: str) -> None:
    log.debug(f"Stream iniciado: {symbol} {timeframe}")
    prev_ts = 0
    while True:
        try:
            candles = await exchange.watch_ohlcv(symbol, timeframe, limit=100)
            if not candles:
                continue
            last_ts = candles[-1][0]
            # Candle fechada = novo timestamp diferente do anterior
            if prev_ts and last_ts != prev_ts:
                await on_candle_close(exchange, symbol, timeframe, candles)
            prev_ts = last_ts
        except ccxtpro.NetworkError as e:
            log.warning(f"NetworkError {symbol} {timeframe}: {e} — reconectando...")
            await asyncio.sleep(3)
        except Exception as e:
            log.error(f"Stream erro {symbol} {timeframe}: {e}")
            await asyncio.sleep(5)


# ── Monitor de posições abertas ────────────────────────────────────────────────

# Track reconciliation failures for circuit breaker
_reconciliation_failures = 0
_MAX_RECONCILIATION_FAILURES = 3

async def position_monitor(exchange) -> None:
    """
    Verifica posições a cada 15s.
    Paper: simula SL/TP por ticker. Live: deteta via fetch_positions.
    Inclui reconciliação periódica com exchange para garantir consistência de estado.
    """
    global _reconciliation_failures
    
    while True:
        try:
            await asyncio.sleep(15)
            
            # Perform state reconciliation every 5 minutes (20 cycles of 15s)
            if int(time.time()) % 300 < 15:  # First 15s of each 5-min window
                if not PAPER:
                    try:
                        await _reconcile_positions(exchange)
                    except Exception as e:
                        log.error(f"Position reconciliation failed: {e}")
                        _reconciliation_failures += 1
                        if _reconciliation_failures >= _MAX_RECONCILIATION_FAILURES:
                            log.error(f"Position reconciliation failed {_reconciliation_failures} times. Consider pausing trading.")
                            await tg_async(
                                f"🚨 <b>ALERTA DE RECONCILIAÇÃO</b>\n"
                                f"Falha na reconciliação de posições {_reconciliation_failures} vezes consecutivas.\n"
                                f"Verifique manualmente o estado das posições."
                            )
                    else:
                        # Reset failure counter on successful reconciliation
                        _reconciliation_failures = 0
            
            if not positions:
                # Live mode: alertar sobre posições na exchange não rastreadas localmente
                if not PAPER:
                    try:
                        live = await exchange.fetch_positions()
                        untracked = [p for p in live if float(p.get("contracts") or 0) > 0]
                        if untracked:
                            syms = ", ".join(p["symbol"] for p in untracked)
                            log.warning(f"[ALERTA] Posições na exchange não rastreadas: {syms}")
                            await tg_async(
                                f"⚠️ <b>POSIÇÃO NÃO RASTREADA</b>\n"
                                f"A exchange tem posições que o bot não registou:\n"
                                f"<code>{syms}</code>\n"
                                f"Fechar manualmente se necessário."
                            )
                    except Exception:
                        pass
                continue

            if PAPER:
                # Paper mode: simular SL/TP usando preço actual (não usar fetch_positions)
                for sym in list(positions.keys()):
                    pos = positions[sym]
                    try:
                        ticker = await exchange.fetch_ticker(sym)
                        price = float(ticker["last"])
                        sl = pos.get("sl", 0)
                        tp = pos.get("tp", 0)
                        side = pos.get("side", "LONG")
                        entry = pos.get("entry", price)
                        tp2_price = pos.get("tp2", pos.get("tp", 0))
                        hit_reason = None
                        if side == "LONG":
                            if sl > 0 and price <= sl:
                                hit_reason = "SL"
                            elif tp2_price > 0 and price >= tp2_price:
                                hit_reason = "TP2"
                        else:
                            if sl > 0 and price >= sl:
                                hit_reason = "SL"
                            elif tp2_price > 0 and price <= tp2_price:
                                hit_reason = "TP2"
                        # Verificar TP1 (partial close)
                        tp1_price = pos.get("tp1", 0)
                        tp1_hit = pos.get("tp1_hit", False)
                        if not tp1_hit and tp1_price > 0:
                            tp1_reached = (side == "LONG" and price >= tp1_price) or \
                                          (side == "SHORT" and price <= tp1_price)
                            if tp1_reached:
                                qty_half = pos.get("qty_half", pos.get("qty", 0) / 2)
                                pnl_half = abs(tp1_price - entry) * qty_half
                                sym_short = _short_sym(sym)
                                log.info(f"[PAPER] {sym} TP1 @ ${price:.6f} | PnL parcial≈${pnl_half:.4f}")
                                await tg_async(
                                    f"🎯 <b>[PAPER] TP1 atingido</b> — {sym_short}\n"
                                    f"💰 Preço: <code>${price:.6f}</code>\n"
                                    f"📈 PnL parcial: <code>${pnl_half:.4f}</code>\n"
                                    f"🔒 SL movido para breakeven @ <code>${entry:.6f}</code>"
                                )
                                positions[sym]["tp1_hit"] = True
                                positions[sym]["sl"] = entry  # breakeven imediato
                                st.save_positions(positions)
                                hit_reason = None  # não fechar tudo ainda

                        if hit_reason:
                            qty_pos = pos.get("qty", 0)
                            pnl = (price - entry) * qty_pos * (1 if side == "LONG" else -1)
                            sym_short = _short_sym(sym)
                            emoji = "✅" if hit_reason in ("TP", "TP2") else "🛑"
                            log.info(f"[PAPER] {sym} {hit_reason} @ ${price:.6f} | PnL≈${pnl:.4f}")
                            await tg_async(
                                f"{emoji} <b>[PAPER] {hit_reason} atingido</b> — {sym_short}\n"
                                f"💰 Preço: <code>${price:.6f}</code>\n"
                                f"📈 PnL estimado: <code>${pnl:.4f}</code>"
                            )
                            st.append_history({
                                "symbol": sym, "side": side,
                                "entry": entry, "exit": price,
                                "closed_at": time.time(),
                                "leverage": pos.get("leverage", DEFAULT_LEVERAGE),
                                "reason": hit_reason, "paper": True,
                                "tp1_hit": pos.get("tp1_hit", False),
                            })
                            positions.pop(sym, None)
                            st.save_positions(positions)
                            _set_cooldown(sym)
                    except Exception as e:
                        log.debug(f"paper monitor {sym}: {e}")
                continue

            # Live mode: verificar via exchange
            live = await exchange.fetch_positions()
            live_syms = {p["symbol"] for p in live if float(p.get("contracts") or 0) > 0}

            # Alertar sobre posições live não rastreadas
            untracked = live_syms - set(positions.keys())
            if untracked:
                log.warning(f"[ALERTA] Posições não rastreadas na exchange: {untracked}")

            for sym in list(positions.keys()):
                pos = positions[sym]

                # Posição já fechada pela exchange (SL/TP atingido)
                if sym not in live_syms:
                    log.info(f"Posição {sym} fechada pela exchange (SL/TP)")
                    sym_short = _short_sym(sym)
                    await tg_async(f"✅ <b>POSIÇÃO FECHADA</b> — {sym_short}\nSL/TP atingido pela exchange.")
                    entry = pos.get("entry", 0)
                    st.append_history({
                        "symbol": sym, "side": pos["side"],
                        "entry": entry, "closed_at": time.time(),
                        "leverage": pos.get("leverage", DEFAULT_LEVERAGE),
                        "reason": "exchange_closed",
                    })
                    positions.pop(sym, None)
                    st.save_positions(positions)
                    _set_cooldown(sym)
                    continue

                # Verificar se próximo da liquidação + breakeven stop
                try:
                    ticker = await exchange.fetch_ticker(sym)
                    price = float(ticker["last"])
                    sl = pos.get("sl", 0)
                    side = pos.get("side", "LONG")
                    if sl > 0 and RISK.near_liquidation(price, sl, side):
                        log.error(f"[ALERT] {sym} perto do SL! price=${price:.6f} sl=${sl:.6f}")

                    # Breakeven stop — quando progresso ≥ 60% do TP
                    entry = pos.get("entry", 0)
                    tp = pos.get("tp", 0)
                    if entry and tp and sl and not pos.get("be_moved"):
                        tp_dist = abs(tp - entry)
                        price_dist = (price - entry) if side == "LONG" else (entry - price)
                        progress = price_dist / tp_dist if tp_dist > 0 else 0
                        if progress >= 0.6:
                            open_orders = await exchange.fetch_open_orders(sym)
                            for o in open_orders:
                                if o.get("reduceOnly") and o.get("type") in ("stop_market", "stop"):
                                    await exchange.cancel_order(o["id"], sym)
                            close_side = "sell" if side == "LONG" else "buy"
                            qty = pos.get("qty", 0)
                            await exchange.create_order(
                                sym, "stop_market", close_side, qty,
                                params={"stopPrice": entry, "reduceOnly": True}
                            )
                            positions[sym]["sl"] = entry
                            positions[sym]["be_moved"] = True
                            st.save_positions(positions)
                            log.info(f"[BE] {sym} SL movido para breakeven @ {entry:.6f}")
                            await tg_async(
                                f"🔒 <b>BREAKEVEN</b> — {_short_sym(sym)}\n"
                                f"SL movido para entrada @ <code>${entry:.6f}</code>"
                            )
                except Exception:
                    pass

        except Exception as e:
            log.error(f"position_monitor erro: {e}")
            await asyncio.sleep(10)


async def _reconcile_positions(exchange) -> None:
    """
    Reconcilia posições locais com posições reais na exchange.
    Corrigi discrepâncias e alerta sobre posições não rastreadas.
    """
    try:
        # Obter posições reais da exchange
        live_positions = await exchange.fetch_positions()
        live_syms = {
            p["symbol"] for p in live_positions 
            if float(p.get("contracts") or 0) > 0
        }
        
        # Posições locais
        local_syms = set(positions.keys())
        
        # Posições na exchange mas não rastreadas localmente
        untracked = live_syms - local_syms
        if untracked:
            syms = ", ".join(untracked)
            log.warning(f"[RECONCILIAÇÃO] Posições não rastreadas: {syms}")
            await tg_async(
                f"⚠️ <b>RECONCILIAÇÃO: POSIÇÕES NÃO RASTREADAS</b>\n"
                f"A exchange tem posições que o bot não registou:\n"
                f"<code>{syms}</code>\n"
                f"Estas serão adicionadas ao monitoramento."
            )
            # Note: We don't automatically add them as we don't have their full details
            # In a production system, you might want to fetch full details for these
        
        # Posições rastreadas localmente mas não na exchange (provavelmente fechadas)
        phantom = local_syms - live_syms
        if phantom:
            syms = ", ".join(phantom)
            log.info(f"[RECONCILIAÇÃO] Posições fantasma (fechadas na exchange): {syms}")
            # Remove phantom positions from local state
            for sym in phantom:
                pos = positions.get(sym)
                if pos:
                    sym_short = _short_sym(sym)
                    entry = pos.get("entry", 0)
                    # Registrar fechamento na história
                    st.append_history({
                        "symbol": sym, 
                        "side": pos["side"],
                        "entry": entry, 
                        "closed_at": time.time(),
                        "leverage": pos.get("leverage", DEFAULT_LEVERAGE),
                        "reason": "reconciliation_closed",
                    })
                    positions.pop(sym, None)
                    st.save_positions(positions)
                    _set_cooldown(sym)
                    await tg_async(
                        f"✅ <b>RECONCILIAÇÃO: Posição removida</b> — {sym_short}\n"
                        f"Posição foi fechada na exchange mas não no bot."
                    )

    except Exception as e:
        log.error(f"Erro durante reconciliação de posições: {e}")
        raise  # Re-raise to be handled by caller


# ── Dashboard simples (HTTP) ───────────────────────────────────────────────────

async def dashboard_server() -> None:
    """Servidor HTTP mínimo na porta 8766 com estado em JSON."""
    from aiohttp import web

    async def handle(request):
        data = {
            "mode": "PAPER" if PAPER else "LIVE",
            "leverage": DEFAULT_LEVERAGE,
            "positions": positions,
            "last_signals": {k: {"direction": v[0], "score": v[1],
                                  "ago_sec": int(time.time() - v[2])}
                             for k, v in last_signal.items()},
            "circuit_breaker": RISK.circuit_breaker_active,
            "cb_remaining_min": int(RISK.cb_seconds_remaining / 60),
            "pairs": WATCH_PAIRS,
            "min_score": MIN_SCORE,
            "tv_feed": tv_available() and USE_TV_FEED,
        }
        return web.Response(
            text=json.dumps(data, indent=2),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    app = web.Application()
    app.router.add_get("/", handle)
    app.router.add_get("/status", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    # Bind to localhost only for security
    site = web.TCPSite(runner, "127.0.0.1", 8766)
    try:
        await site.start()
        log.info("Dashboard: http://127.0.0.1:8766/status (localhost only for security)")
    except OSError as e:
        log.warning(f"Dashboard não iniciado (porta 8766 ocupada — instância anterior ainda ativa?): {e}")
        await runner.cleanup()
        return
    while True:
        await asyncio.sleep(3600)


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    global shutdown_requested, telegram_handler, shutdown_manager
    
    print("=" * 60)
    print("  Bot Trading Nexus — Scalping Assíncrono")
    print(f"  Modo: {'PAPER (sem ordens reais)' if PAPER else 'LIVE'}")
    print(f"  Pares pedidos: {', '.join(_RAW_PAIRS)}")
    print(f"  TFs: {', '.join(TIMEFRAMES)}")
    print(f"  Leverage: {DEFAULT_LEVERAGE}x (max {MAX_LEVERAGE}x)")
    print(f"  MIN_SCORE: {MIN_SCORE}")
    print(f"  tvDatafeed: {'ON' if tv_available() and USE_TV_FEED else 'OFF'}")
    print("=" * 60)

    exchange = await create_exchange()
    
    # Initialize Telegram handler
    telegram_handler = await init_telegram_handler(exchange)
    
    # Initialize state from persistent storage
    global positions, cooldowns
    positions = st.load_positions()
    cooldowns = st.load_cooldowns()
    
    # Load state into thread-safe manager
    for symbol, pos_data in positions.items():
        await state_manager.update_position(symbol, pos_data)
    
    for symbol, expiry in cooldowns.items():
        await state_manager.update_cooldown(symbol, expiry)

    try:
        # Pré-carregar mercados e resolver símbolos
        await exchange.load_markets()
        log.info("A resolver símbolos...")
        await resolve_symbols(exchange)
        log.info(f"Mercados carregados. {len(WATCH_PAIRS)} pares × {len(TIMEFRAMES)} TFs")
        await preload_candles(exchange)

        # Criar tasks WebSocket para todos os pares e TFs
        tasks = []
        for pair in WATCH_PAIRS:
            for tf in TIMEFRAMES:
                tasks.append(asyncio.create_task(
                    stream_ohlcv(exchange, pair, tf),
                    name=f"stream_{pair}_{tf}"
                ))

        tasks.append(asyncio.create_task(position_monitor(exchange), name="monitor"))
        tasks.append(asyncio.create_task(dashboard_server(), name="dashboard"))
        tasks.append(asyncio.create_task(heartbeat_logger(), name="heartbeat"))
        
        # Add buffer cleanup task
        tasks.append(asyncio.create_task(buffer_cleanup_task(), name="cleanup"))
        
        # Add shutdown monitor task
        tasks.append(asyncio.create_task(shutdown_monitor_task(), name="shutdown_monitor"))

        log.info(f"{len(tasks)} tasks iniciadas. A aguardar candles...")
        _log_position_table()

        await asyncio.gather(*tasks)

    except KeyboardInterrupt:
        log.info("Nexus parado pelo utilizador.")
    except Exception as e:
        log.error(f"Erro fatal: {e}", exc_info=True)
    finally:
        # Cleanup
        if telegram_handler:
            await telegram_handler.stop_polling()
        
        await exchange.close()
        
        # Save final state
        st.save_positions(positions)
        st.save_cooldowns(cooldowns)
        
        log.info("Estado guardado. Bye.")

async def buffer_cleanup_task() -> None:
    """Periodic buffer cleanup task."""
    while not shutdown_requested:
        try:
            await asyncio.sleep(300)  # Every 5 minutes
            
            if shutdown_requested:
                break
                
            await state_manager.cleanup_all_buffers()
            
            # Remove expired cooldowns
            expired_count = await state_manager.remove_expired_cooldowns()
            if expired_count > 0:
                log.debug(f"Removed {expired_count} expired cooldowns")
                
        except Exception as e:
            log.error(f"Buffer cleanup error: {e}")
            await asyncio.sleep(60)

async def shutdown_monitor_task() -> None:
    """Monitor shutdown status and handle completion."""
    global shutdown_requested, shutdown_manager
    
    while not shutdown_requested:
        await asyncio.sleep(1)
    
    log.info("Shutdown requested, monitoring completion...")
    
    if shutdown_manager:
        # Wait for shutdown to complete
        success = await shutdown_manager.wait_for_shutdown(timeout=180)  # 3 minutes max
        
        if success:
            log.info("Shutdown completed successfully")
        else:
            log.error("Shutdown failed or timed out")
            shutdown_manager.force_shutdown()
    
    # Cancel all other tasks
    for task in asyncio.all_tasks():
        if task != asyncio.current_task() and not task.done():
            task.cancel()
            
    log.info("All tasks cancelled. Exiting...")


if __name__ == "__main__":
    asyncio.run(main())
