"""
Bot Trading Nexus — Persistência de Estado
Escrita atómica via .tmp + rename (idêntico ao bot atual).
"""
from __future__ import annotations
import json
import os
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger("nexus.state")

_HERE = Path(__file__).parent

STATE_FILE    = _HERE / ".nexus_state.json"
HISTORY_FILE  = _HERE / ".nexus_history.json"
COOLDOWN_FILE = _HERE / ".nexus_cooldowns.json"
DAILY_FILE    = _HERE / ".nexus_daily.json"


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_positions() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception as e:
            log.error(f"Erro ao ler state: {e}")
    return {}


def save_positions(positions: dict) -> None:
    _atomic_write(STATE_FILE, positions)


def load_daily() -> dict:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if DAILY_FILE.exists():
        try:
            data = json.loads(DAILY_FILE.read_text())
            if data.get("date") == today:
                return data
        except Exception:
            pass
    return {"date": today, "start_balance": 0.0, "realized_pnl": 0.0, "trades": 0}


def save_daily(data: dict) -> None:
    _atomic_write(DAILY_FILE, data)


def load_cooldowns() -> dict:
    if COOLDOWN_FILE.exists():
        try:
            return json.loads(COOLDOWN_FILE.read_text())
        except Exception:
            pass
    return {}


def save_cooldowns(cooldowns: dict) -> None:
    _atomic_write(COOLDOWN_FILE, cooldowns)


def append_history(trade: dict) -> None:
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text())
        except Exception:
            pass
    history.append(trade)
    if len(history) > 500:
        history = history[-500:]
    _atomic_write(HISTORY_FILE, history)


def load_history() -> list:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            pass
    return []
