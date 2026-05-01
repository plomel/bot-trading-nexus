#!/usr/bin/env python3
"""
Nexus Bot Launcher
Inicia o bot de trading scalping (meme coins) com opção paper/live mode.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
NEXUS_BOT = HERE / "nexus.py"


def main():
    print("=" * 55)
    print("  Nexus Bot Launcher")
    print("=" * 55)
    print("\nBot scalping async — meme coins (1m/3m/5m/15m/1h)")
    print()

    if not NEXUS_BOT.exists():
        print(f"❌ Não encontrado: {NEXUS_BOT}")
        input("\n[ENTER para fechar]")
        return

    print("Modo de execução:")
    print("  [1] Paper mode (simulação sem dinheiro real)")
    print("  [2] Live mode (trades reais com Binance)")
    print()

    choice = input("Seleciona (1/2, ENTER=1): ").strip()
    mode = "paper" if choice != "2" else "live"

    flag = " --paper" if mode == "paper" else ""
    mode_display = "(PAPER)" if mode == "paper" else "(LIVE)"

    print(f"\nIniciando Nexus Bot {mode_display}...")
    print(f"Comando: python nexus.py{flag}")
    print()

    subprocess.Popen(
        [sys.executable, str(NEXUS_BOT)] + (["--paper"] if mode == "paper" else []),
        cwd=HERE,
        creationflags=subprocess.CREATE_NEW_CONSOLE
    )

    print(f"✅ Nexus Bot iniciado {mode_display}")
    print(f"   Dashboard: http://localhost:8766/status (opcional)")
    print()
    print("[fecha esta janela quando quiseres]")
    input()


if __name__ == "__main__":
    main()
