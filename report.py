"""Relatório de economia do router. Puro: lê usage.jsonl, sem rede, sem LLM, sem Hermes.

Usado por `/jev relatorio`, pela skill `jev-hermes-router:relatorio` e pelo cron diário.

    python3 report.py                 # últimas 24h deste Hermes
    python3 report.py --janela 7d     # 24h · 7d · 30d · tudo
    python3 report.py --todos-perfis  # soma ~/.hermes + ~/.hermes/profiles/*

Como a economia é medida
------------------------
Assinatura (ChatGPT/Claude) não cobra por token, cobra em cota. Não existe API pública
de cota, então o proxy é o preço de lista da API de cada modelo: quanto aquelas chamadas
custariam em dólar. Comparamos duas contas, chamada por chamada:

  real      = tokens que rodaram, no preço do modelo que o router aplicou
  sem jev   = os mesmos tokens no modelo da sessão (o que rodaria sem o plugin)

O "sem jev" é generoso com o modelo da sessão: assume que o cache dele estaria sempre
quente (tudo que a chamada anterior já tinha mandado conta como cache lido). Isso cobra
do router o custo real de trocar de modelo, que esfria o cache. Por isso o número é
conservador. O custo do Jev entra do lado "real".
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

# US$ por milhão de tokens: (entrada, cache lido, saída). Preço de lista da API, set/2026.
# Proxy de cota, não conta. Reconhece o modelo pela família (luna, sol, astra, haiku...).
PRICES = {
    "luna": (0.10, 0.01, 0.50),
    "sol": (2.00, 0.20, 10.00),
    "astra": (10.00, 1.00, 50.00),
    "haiku": (1.00, 0.10, 5.00),
    "sonnet": (2.00, 0.20, 10.00),
    "opus": (5.00, 0.50, 25.00),
    "fable": (10.00, 0.25, 50.00),
}
TOPO = {"openai-codex": PRICES["astra"], "anthropic": PRICES["fable"]}  # degrau máximo do ladder
JEV_PER_M = 0.042  # entrada do Jev na TypeSafe
TIERS = ("leve", "padrao", "pesado", "maximo")
JANELAS = {"24h": 86400, "hoje": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "tudo": None}


def price_of(model: str | None):
    m = (model or "").lower()
    for family, p in PRICES.items():
        if family in m:
            return p
    return None


def _cost(p, fresh: float, cached: float, out: float) -> float:
    return (fresh * p[0] + cached * p[1] + out * p[2]) / 1e6


@dataclass
class Resumo:
    rotulo: str = ""
    chamadas: int = 0
    turnos: int = 0
    por_tier: dict = field(default_factory=dict)
    trocas: int = 0
    real: float = 0.0
    sem_jev: float = 0.0
    jev: float = 0.0
    prompt: int = 0
    cache: int = 0
    jev_ms: list = field(default_factory=list)
    desceu: list = field(default_factory=lambda: [0, 0.0, 0.0])   # [chamadas, real, sem jev]
    subiu: list = field(default_factory=lambda: [0, 0.0, 0.0])
    topo: float = 0.0      # mesmos tokens, tudo no degrau máximo do provedor
    sem_saida: int = 0     # linhas antigas sem tokens de saída (bug corrigido na 0.2.1)
    sem_rota: int = 0      # erro do Jev ou fora do ladder

    @property
    def economia(self) -> float | None:
        return 1 - (self.real + self.jev) / self.sem_jev if self.sem_jev else None

    @property
    def economia_topo(self) -> float | None:
        return 1 - (self.real + self.jev) / self.topo if self.topo else None

    @property
    def economia_descidas(self) -> float | None:
        n, real, base = self.desceu
        return 1 - real / base if base else None


def load(path: Path, desde: float | None) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if desde is None or (r.get("ts") or 0) >= desde:
            rows.append(r)
    return sorted(rows, key=lambda r: r.get("ts") or 0)


def resumir(rows: list[dict], rotulo: str = "") -> Resumo:
    s = Resumo(rotulo=rotulo)
    prev_prompt: dict[str, int] = {}
    prev_tier: dict[str, str] = {}
    last_turn = None
    for r in rows:
        sess = str(r.get("session") or "")
        s.chamadas += 1
        tier = r.get("tier")
        if not tier:
            s.sem_rota += 1
            continue
        # turno novo = primeira chamada do turno; linha antiga (sem "call"): mudou a decisão do Jev
        if "call" in r:
            novo_turno = r["call"] == 1
        else:
            key = (sess, r.get("jev_ms"), r.get("jev_tokens"), tier)
            novo_turno, last_turn = key != last_turn, key
        if novo_turno:
            s.turnos += 1
            s.por_tier[tier] = s.por_tier.get(tier, 0) + 1
            s.jev += (r.get("jev_tokens") or 0) * JEV_PER_M / 1e6
            if r.get("jev_ms"):
                s.jev_ms.append(r["jev_ms"])
            if sess in prev_tier and prev_tier[sess] != tier:
                s.trocas += 1
            prev_tier[sess] = tier
        pt = int(r.get("prompt_tokens") or 0)
        cr = min(int(r.get("cache_read") or 0), pt)
        out = r.get("completion_tokens")
        if out is None:
            s.sem_saida += 1
            out = 0
        p_run = price_of(r.get("model") or r.get("applied"))
        p_base = price_of(r.get("session_model"))
        if p_run and p_base and pt:
            c_real = _cost(p_run, pt - cr, cr, out)
            warm = max(cr, min(prev_prompt.get(sess, 0), pt))  # sessão sem jev: cache sempre quente
            c_base = _cost(p_base, pt - warm, warm, out)
            s.real += c_real
            s.sem_jev += c_base
            s.topo += _cost(TOPO.get(r.get("provider") or "", p_base), pt - warm, warm, out)
            if p_run != p_base:
                b = s.desceu if p_run[0] < p_base[0] else s.subiu
                b[0] += 1; b[1] += c_real; b[2] += c_base
            s.prompt += pt
            s.cache += cr
        prev_prompt[sess] = pt
    return s


def _usd(v: float) -> str:
    return f"US$ {v:.2f}".replace(".", ",") if v >= 0.01 else f"US$ {v:.4f}".replace(".", ",")


def formatar(s: Resumo, janela: str) -> str:
    nome = {"24h": "últimas 24h", "hoje": "últimas 24h", "7d": "últimos 7 dias", "30d": "últimos 30 dias", "tudo": "desde a instalação"}[janela]
    cab = f"⚙️ Jev · {nome}" + (f" · {s.rotulo}" if s.rotulo else "")
    if not s.chamadas:
        return f"{cab}\nNenhuma mensagem roteada no período. O relatório roda mesmo assim, pra você saber que está vivo."
    dist = " · ".join(f"{t} {s.por_tier.get(t, 0)}" for t in TIERS)
    linhas = [cab, f"{s.turnos} mensagens, {s.chamadas} chamadas ao modelo · {dist}"]
    if s.sem_jev:
        linhas.append(f"Consumo: {_usd(s.real + s.jev)} equivalentes, contra {_usd(s.sem_jev)} se tudo rodasse no modelo da sessão")
        sinal = "Economia" if s.economia >= 0 else "Gasto extra"
        linhas.append(f"{sinal} líquido vs modelo da sessão: {abs(s.economia) * 100:.0f}% (já cobra o cache perdido nas trocas)")
        n, real, base = s.desceu
        if n:
            linhas.append(f"  ↓ {n} chamadas desceram de modelo: {_usd(real)} em vez de {_usd(base)} ({s.economia_descidas * 100:.0f}% a menos)")
        n, real, base = s.subiu
        if n:
            linhas.append(f"  ↑ {n} chamadas subiram de modelo: {_usd(real)} em vez de {_usd(base)} (+{_usd(real - base)} pra ter o modelo mais forte)")
        if s.topo and s.economia_topo is not None:
            e = s.economia_topo * 100
            linhas.append(f"Contra deixar tudo no modelo máximo: {abs(e):.0f}% {'a menos' if e >= 0 else 'a mais'}")
    cache = f"{100 * s.cache / s.prompt:.0f}%" if s.prompt else "sem dado"
    ms = f"{sum(s.jev_ms) // len(s.jev_ms)} ms" if s.jev_ms else "sem dado"
    linhas.append(f"Trocas de modelo: {s.trocas} · cache lido: {cache} · Jev: {ms}, {_usd(s.jev)}")
    notas = []
    if s.turnos < 50:
        notas.append(f"amostra pequena ({s.turnos} mensagens), não tire conclusão ainda")
    if s.sem_rota:
        notas.append(f"{s.sem_rota} chamadas sem rota (erro do Jev ou modelo fora do ladder)")
    if s.sem_saida:
        notas.append(f"{s.sem_saida} chamadas antigas sem tokens de saída, contadas só pela entrada")
    if notas:
        linhas.append("Obs.: " + "; ".join(notas) + ".")
    linhas.append("Dólar aqui é preço de lista da API, usado como proxy de cota. Assinatura não cobra por token.")
    return "\n".join(linhas)


def homes(root: Path, todos: bool) -> list[tuple[str, Path]]:
    out = [("", root)]
    if todos and (root / "profiles").is_dir():
        out = [("default", root)] + [(p.name, p) for p in sorted((root / "profiles").iterdir()) if p.is_dir()]
    return out


def relatorio(root: Path, janela: str = "24h", todos: bool = False, now: float | None = None) -> str:
    seg = JANELAS[janela]
    desde = None if seg is None else (now or time.time()) - seg
    achados = []
    for nome, home in homes(root, todos):
        rows = load(home / "plugin-data" / "jev-hermes-router" / "usage.jsonl", desde)
        if rows or not todos:
            achados.append(resumir(rows, nome))
    if len(achados) == 1:
        achados[0].rotulo = ""  # um perfil só: sem rótulo
    if not achados:
        return formatar(Resumo(), janela)
    return "\n\n".join(formatar(s, janela) for s in achados)


def main() -> None:
    ap = argparse.ArgumentParser(description="Relatório de economia do jev-hermes-router")
    ap.add_argument("--janela", default="24h", choices=sorted(JANELAS))
    ap.add_argument("--home", default=os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes"))
    ap.add_argument("--todos-perfis", action="store_true")
    a = ap.parse_args()
    print(relatorio(Path(a.home), a.janela, a.todos_perfis))


if __name__ == "__main__":
    main()
