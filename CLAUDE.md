## Claude Context

**Última atualização:** 2026-04-28
**Status:** 🟡 Paper trading — a aguardar 24h de dados para calibrar

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

**Config activa:**
- MIN_SCORE: 45 | Leverage: 10x | Risco: 1%/trade
- MAX_POSITIONS: 5 | Máx meme coins: 2 simultâneas
- Heartbeat Telegram: cada 15min
- Dashboard: localhost:8766/status

**Próximos passos:**
1. Paper trading 24h — verificar entradas/saídas reais nos logs
2. Calibrar MIN_SCORE (pode subir para 55-60 após observar frequência de sinais)
3. Go live com €20-30 após paper estável

**Notas de risco:**
- 10x leverage: SL de 0.5% = 5% da margem por trade
- Funding rate meme coins pode ser alto — bot verifica antes de LONG
- PEPE/BONK/SHIB/FLOKI/BOME/WIF correlacionados — máx 2 simultâneas
