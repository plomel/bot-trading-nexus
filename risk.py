"""
NexusScalpTrading Bot — Gestão de Risco
Gestão conservadora: 1% risco/trade, leverage por categoria (meme 3x / main 5x).
Circuit breaker duplo: emergências + perda horária de 3%.
"""
from __future__ import annotations
import time
import logging

log = logging.getLogger("nexus.risk")


class RiskManager:
    """
    Controla sizing, circuit breaker e daily loss cap.
    Todos os parâmetros vêm do .env via config dict.
    """

    def __init__(self, cfg: dict):
        self.max_risk_pct   = float(cfg.get("MAX_RISK_PER_TRADE", 1.0))    # % conta por trade
        self.daily_loss_cap = float(cfg.get("MAX_DAILY_LOSS", 8.0))         # % diária
        self.cb_emerg_count = int(cfg.get("CB_EMERG_COUNT", 2))             # emergências que ativam CB
        self.cb_window_sec  = int(cfg.get("CB_WINDOW_SEC", 1200))           # janela 20min
        self.cb_pause_sec   = int(cfg.get("CB_PAUSE_SEC", 7200))            # pausa 2h
        self.max_margin_pct = float(cfg.get("MAX_MARGIN_PCT", 5.0))         # % conta em margem por pos
        self.liq_buffer_pct = float(cfg.get("LIQUIDATION_BUFFER", 0.15))    # % buffer de liquidação

        self.hourly_loss_trigger = float(cfg.get("HOURLY_LOSS_TRIGGER", 3.0))

        self._emergency_times: list[float] = []
        self._cb_until: float = 0.0
        self._daily_loss: float = 0.0
        self._daily_start_balance: float = 0.0
        self._day_key: str = ""
        self._balance_snapshots: list[tuple[float, float]] = []  # (timestamp, balance)

    # ── Circuit Breaker ────────────────────────────────────────────────────────

    def register_emergency(self) -> None:
        now = time.time()
        self._emergency_times = [t for t in self._emergency_times
                                  if now - t < self.cb_window_sec]
        self._emergency_times.append(now)
        if len(self._emergency_times) >= self.cb_emerg_count:
            self._cb_until = now + self.cb_pause_sec
            log.error(f"[CB] Circuit breaker ativado! Pause até "
                      f"{time.strftime('%H:%M', time.localtime(self._cb_until))}")

    @property
    def circuit_breaker_active(self) -> bool:
        return time.time() < self._cb_until

    @property
    def cb_seconds_remaining(self) -> float:
        return max(0.0, self._cb_until - time.time())

    # ── Daily Loss ─────────────────────────────────────────────────────────────

    def update_daily(self, current_balance: float, day_key: str) -> None:
        if day_key != self._day_key:
            self._day_key = day_key
            self._daily_start_balance = current_balance
            self._daily_loss = 0.0
        if self._daily_start_balance > 0:
            loss_pct = (self._daily_start_balance - current_balance) / self._daily_start_balance * 100
            self._daily_loss = max(0.0, loss_pct)

        # Regista snapshot horário — mantém apenas últimas 2 horas
        now = time.time()
        self._balance_snapshots.append((now, current_balance))
        self._balance_snapshots = [(t, b) for t, b in self._balance_snapshots if now - t < 7200]

    @property
    def daily_loss_exceeded(self) -> bool:
        return self._daily_loss >= self.daily_loss_cap

    @property
    def hourly_loss_exceeded(self) -> bool:
        """True se a conta perdeu mais de hourly_loss_trigger% na última hora."""
        if len(self._balance_snapshots) < 2:
            return False
        now = time.time()
        one_hour_ago = now - 3600
        old = [b for t, b in self._balance_snapshots if t <= one_hour_ago]
        if not old:
            return False
        balance_1h_ago = old[-1]
        current = self._balance_snapshots[-1][1]
        if balance_1h_ago <= 0:
            return False
        loss_pct = (balance_1h_ago - current) / balance_1h_ago * 100
        return loss_pct >= self.hourly_loss_trigger

    # ── Entry Gate ─────────────────────────────────────────────────────────────

    def can_enter(self) -> tuple[bool, str]:
        if self.circuit_breaker_active:
            mins = int(self.cb_seconds_remaining / 60)
            return False, f"Circuit breaker ativo — {mins}min restantes"
        if self.daily_loss_exceeded:
            return False, f"Daily loss cap atingido ({self._daily_loss:.1f}%)"
        if self.hourly_loss_exceeded:
            log.error(f"[CB] Perda horária >{self.hourly_loss_trigger:.0f}% — ativando circuit breaker")
            self._cb_until = time.time() + self.cb_pause_sec
            return False, f"Perda horária >{self.hourly_loss_trigger:.0f}% — circuit breaker ativado"
        return True, "ok"

    # ── Position Sizing ────────────────────────────────────────────────────────

    def compute_qty(self, price: float, sl_price: float, side: str,
                    balance: float, leverage: int) -> float:
        """
        Calcula quantidade baseada em risco fixo por trade.
        Nunca excede max_margin_pct da conta em margem.
        """
        if price <= 0 or sl_price <= 0:
            return 0.0

        sl_distance = abs(price - sl_price) / price  # fracção do preço
        if sl_distance < 0.003:  # mínimo 0.3% — cobre spread + fees round-trip em meme coins
            log.warning(f"SL demasiado apertado ({sl_distance*100:.3f}%) — ignorado (mín 0.3%)")
            return 0.0

        risk_usd = balance * (self.max_risk_pct / 100)
        qty_by_risk = risk_usd / (price * sl_distance)

        # Cap por margem: nunca colocar mais de max_margin_pct em margem
        max_notional = balance * (self.max_margin_pct / 100) * leverage
        qty_by_margin = max_notional / price

        qty = min(qty_by_risk, qty_by_margin)
        return float(qty)

    # ── SL/TP Dinâmico por ATR ─────────────────────────────────────────────────

    @staticmethod
    def compute_sl_tp(price: float, side: str, atr: float,
                      sl_mult: float = 0.8, tp_mult: float = 1.6) -> tuple[float, float]:
        """
        SL/TP baseado em ATR para scalping.
        Rácio TP/SL = 2:1 por default.
        sl_mult=0.8 × ATR (conservador para alta leverage)
        """
        sl_dist = atr * sl_mult
        fee_round_trip = 0.10 / 100   # 0.05% taker × 2 lados
        min_tp_dist = sl_dist * 2 + fee_round_trip * price
        tp_dist = max(atr * tp_mult, min_tp_dist)

        if side == "LONG":
            sl = price - sl_dist
            tp = price + tp_dist
        else:
            sl = price + sl_dist
            tp = price - tp_dist

        return round(sl, 8), round(tp, 8)

    # ── Partial TP Levels ──────────────────────────────────────────────────────

    @staticmethod
    def compute_tp_levels(price: float, side: str, sl_price: float) -> tuple[float, float]:
        """
        Retorna (tp1, tp2) para partial TP.
        tp1 = 1:1 R:R → fechar 50% da posição (assegura lucro)
        tp2 = 2:1 R:R → fechar os restantes 50% (maximiza ganho)
        """
        sl_dist = abs(price - sl_price)
        if side == "LONG":
            tp1 = round(price + sl_dist, 8)
            tp2 = round(price + sl_dist * 2, 8)
        else:
            tp1 = round(price - sl_dist, 8)
            tp2 = round(price - sl_dist * 2, 8)
        return tp1, tp2

    # ── Liquidation Check ──────────────────────────────────────────────────────

    def near_liquidation(self, current_price: float, sl_price: float, side: str) -> bool:
        """Retorna True se o preço estiver dentro do buffer de liquidação."""
        dist = abs(current_price - sl_price) / current_price * 100
        return dist < self.liq_buffer_pct
