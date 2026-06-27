## Claude Context — NexusScalpTrading Bot

**Última atualização:** 2026-05-07
**Status:** 🔴 Paper trading parado — fix de símbolos aplicado, precisa de ser reiniciado

**Arquitetura:**
- `nexus.py` — bot principal async (asyncio + ccxt.pro WebSocket)
- `indicators.py` — EMA 3/8/21, RSI 7 (Wilder), VWAP intraday, BBands, ADX, ATR True Range, Delta Volume
- `risk.py` — sizing 1% risco, fee buffer TP, circuit breaker, daily loss cap
- `tv_feed.py` — stub (tvDatafeed removido — não instalável, dados Binance WS suficientes)
- `state.py` — persistência atómica

**Iniciar:**
```
python nexus.py --paper   # paper mode
python nexus.py           # live
```

**Bugs críticos corrigidos (sessão 2026-04-28):**
- Buffer WS substituía REST preload → candles nunca analisados (silêncio total)
- Paper mode fechava posições em 15s (comparava contra Binance real)
- Porta 8766 crashava todos os streams se ocupada
- Timestamp -1021 (`adjustForTimeDifference: True` + `recvWindow: 10000`)
- MEME_GROUP usava prefixos errados (`"1000PEPE"` vs `"PEPE"`)

**Bug corrigido (sessão 2026-05-07):**
- `resolve_symbols()` tentava `PEPE/USDT:USDT` antes de `1000PEPE/USDT:USDT`
- Resultado: PEPE, BONK, SHIB, FLOKI falhavam em loop nos streams WebSocket
- Fix: para `MEME_PAIRS`, candidatos reordenados — `1000{raw}/USDT:USDT` tenta-se primeiro

**Config activa:**
- MIN_SCORE_MAIN: 45 | MIN_SCORE_MEME: 62 | Leverage: 10x | Risco: 1%/trade
- MAX_POSITIONS: 5 | Máx meme coins: 2 simultâneas
- Heartbeat Telegram: cada 15min
- Dashboard: localhost:8766/status

**Estado do paper trading (2026-05-07):**
- `.nexus_history.json`: apenas 2 trades, ambas fechadas por `exchange_closed` (sem PnL)
- `.nexus_state.json`: vazio — sem posições abertas
- **Dados insuficientes para go live** — mínimo 20 trades com TP/SL real necessários

**Próximos passos:**
1. Reiniciar `python nexus.py --paper` — fix de símbolos deve resolver os erros de stream
2. Acumular 20+ trades paper com resultado real (TP ou SL, não exchange_closed)
3. Calcular win rate e drawdown — se win rate ≥ 50%, subir MIN_SCORE_MAIN para 52
4. Go live com €20-30 apenas após critérios cumpridos

**Notas de risco:**
- 10x leverage: SL de 0.5% = 5% da margem por trade
- Funding rate meme coins pode ser alto — bot verifica antes de LONG
- PEPE/BONK/SHIB/FLOKI/BOME/WIF correlacionados — máx 2 simultâneas
