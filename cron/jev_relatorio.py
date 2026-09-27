"""Cron diário do jev-hermes-router: imprime o relatório das últimas 24h.

Fica em ~/.hermes/scripts/ (exigência do cron do Hermes) e só delega pro report.py
do plugin instalado, pra atualização do plugin valer sem recopiar este arquivo.

    hermes cron create "0 21 * * *" --name jev-relatorio --no-agent \
      --script jev_relatorio.py --deliver origin --failure-deliver local

Relatório de medição fala todo dia, inclusive quando não houve mensagem:
silêncio seria indistinguível de cron quebrado.
"""
import os
import runpy
import sys
from pathlib import Path

HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
REPORT = HOME / "plugins" / "jev-hermes-router" / "report.py"

if __name__ == "__main__":
    if not REPORT.exists():
        print(f"⚠️ jev-relatorio: não achei {REPORT}. O plugin foi removido?")
        sys.exit(1)
    extra = [a for a in sys.argv[1:]] or ["--janela", "24h", "--todos-perfis"]
    sys.argv = [str(REPORT), "--home", str(HOME)] + extra
    runpy.run_path(str(REPORT), run_name="__main__")
