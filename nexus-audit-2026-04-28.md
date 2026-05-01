# Nexus — Audit de Scalping (2026-04-28)

> Análise quant completa dos problemas do Bot Trading Nexus para timeframes curtos (1m-5m).
> A implementar na próxima sessão por ordem de prioridade.

---

## Problemas Críticos (perda garantida se não corrigidos)

### 1. Fee blindness — o TP não cobre os custos
**Risco:** SL=0.5%, TP=1.0%. Binance cobra 0.05% taker × 2 lados = 0.10% total/trade.
**Impacto:** Com 50% win rate e rácio 2:1 → edge = -0.10%/trade. **Precisa >57% win rate só para break even.**
**Fix:** `risk.py > compute_sl_tp` — adicionar fee buffer ao TP mínimo:
```python
fee_round_trip = 0.10 / 100
min_tp_dist = sl_dist * 2 + fee_round_trip * price
tp_dist = max(atr * tp_mult, min_tp_dist)
```

---

### 2. Correlação meme coins — 5 posições = 1 posição
**Risco:** PEPE, BONK, SHIB, FLOKI correlação >0.85. Todos caem juntos com BTC.
**Impacto:** 5 posições SHORT simultâneas = exposição concentrada 5×. Spike de 2% elimina 10% da conta.
**Fix:** `nexus.py > open_position` — máx 2 meme coins em simultâneo:
```python
MEME_GROUP = {"1000PEPE", "1000BONK", "1000SHIB", "1000FLOKI", "BOME", "WIF"}
meme_open = sum(1 for s in positions if s.split("/")[0] in MEME_GROUP)
if meme_open >= 2:
    log.info(f"Skip {sym_short} — já {meme_open} meme coins abertas (correlação)")
    return
```

---

### 3. ATR calculado errado — SL mal dimensionado
**Risco:** Usa `abs(high-low)` simples. True Range correto = `max(H-L, |H-C_prev|, |L-C_prev|)`.
**Impacto:** ATR subestimado → SL demasiado apertado → stop hunt imediato em 60-70% dos trades.
**Fix:** Novo `atr()` em `indicators.py`:
```python
def atr(high, low, close, period=7):
    tr = np.maximum(high[1:]-low[1:],
         np.maximum(np.abs(high[1:]-close[:-1]),
                    np.abs(low[1:]-close[:-1])))
    return float(_smooth(tr, period)[-1])
```
Usar em `on_candle_close` em vez do cálculo manual de `mean(high-low)`.

---

### 4. Sem trailing stop — transforma winners em losers
**Risco:** Trade move 0.8% a favor, reverte, bate SL. Lucro potencial virou perda.
**Impacto:** Win rate esperada cai ~15-20% vs estratégia com breakeven stop.
**Fix:** Em `position_monitor` — quando progresso ≥ 60% do TP, mover SL para breakeven:
```python
progress = abs(price - entry) / abs(tp - entry)
if progress >= 0.6 and sl != entry:
    # cancel old SL order, place new SL at entry price
    new_sl = entry
```

---

## Problemas Sérios (reduzem win rate)

### 5. RSI sem smoothing de Wilder
**Problema:** Usa média simples. RSI real usa EMA com `alpha=1/period`. Diferença de 10-15 pts em 1m.
**Fix:** `indicators.py > rsi()` — implementar Wilder smoothing com warmup de `period × 3` candles.

---

### 6. VWAP inconsistente entre TFs
**Problema:** VWAP calculado sobre 100 candles de cada TF → 1m = 100min, 1h = 4 dias. Incomparável.
**Fix:** Filtrar apenas candles do dia atual (desde meia-noite UTC) antes de calcular VWAP.
```python
midnight_ms = (int(time.time()) // 86400) * 86400 * 1000
today_candles = [c for c in ohlcv if c[0] >= midnight_ms]
```

---

### 7. EMA 3 é ruído puro em meme coins 1m
**Problema:** Período 3 reage a cada wick. Gera crossovers falsos constantemente.
**Fix:** Mudar para EMA 5/13/21 (scalping) ou 8/21/55 (mais suave).

---

## O que está bem ✅

- Circuit breaker com janela temporal
- Daily loss cap
- Sizing por risco fixo com cap de margem
- Preload histórico via REST no arranque
- WebSocket real-time (não polling)
- Confirmação de tendência no 1h

---

## Prioridade de Implementação

| # | Fix | Ficheiro | Esforço |
|---|---|---|---|
| 1 | Fee buffer no TP | `risk.py` | 10 min |
| 2 | Correlação meme coins (máx 2) | `nexus.py` | 15 min |
| 3 | ATR com True Range | `indicators.py` | 20 min |
| 4 | Trailing/breakeven stop | `nexus.py` | 30 min |
| 5 | RSI Wilder | `indicators.py` | 20 min |
| 6 | VWAP intraday | `indicators.py` | 20 min |

**Total estimado:** ~2h de implementação

---

*Audit feito: 2026-04-28 · Implementar na próxima sessão*
