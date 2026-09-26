"""A política: perguntas, ladders e a decisão. Código puro, sem rede, sem Hermes.

Validada em 24/09/2026 contra 162 turnos reais (ver PRD). Os números aqui
são o produto; mudar um deles é mudar o comportamento.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TIERS = ("leve", "padrao", "pesado", "maximo")
EFFORTS = ("low", "medium", "high", "xhigh")

LADDERS = {
    "openai-codex": ("gpt-6-luna", "gpt-6-terra", "gpt-6-sol", "gpt-6-astra"),
    "anthropic": ("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5", "claude-fable-5-1"),
}
# Família → degrau, pra reconhecer o modelo da sessão mesmo com sufixo (-900k, -5-5, data).
FAMILY_TIER = {
    "openai-codex": (("luna", 0), ("terra", 1), ("sol", 2), ("astra", 3)),
    "anthropic": (("haiku", 0), ("sonnet", 1), ("opus", 2), ("fable", 3)),
}

# Barras (massa de probabilidade), não confiança do argmax.
DOWN_MASS = 0.60      # desce um degrau se P(tier ≤ degrau) ≥ 0.60
UP_MASS = 0.50        # sobe até o degrau mais alto com P(tier ≥ degrau) ≥ 0.50
CONT_HOLD = 0.50      # continuação ≥ 0.50 → nunca desce
STAKES_FLOOR = 0.80   # risco ≥ 0.80 → nunca desce, esforço mínimo 1
CAP_CHARS = 2000

TIER_CRITERIA = {
    "leve": (
        "Trivial. A one-word or one-line answer, a lookup, a simple rename, "
        "a tiny edit, a yes/no. No judgment needed."
    ),
    "padrao": (
        "Straightforward work with no real decision to make: a short text, "
        "a simple edit, a routine explanation, a small well-specified task."
    ),
    "pesado": (
        "Substantial work that carries some complexity or nuance: writing a "
        "full piece in a specific voice, implementing a feature, analysing a "
        "document, building a spreadsheet or a multi-step task."
    ),
    "maximo": (
        "Planning, architecture, strategy, decisions with tradeoffs, "
        "systematic debugging of something puzzling, legal or financial "
        "review where a mistake is costly. Needs the strongest model."
    ),
}
EFFORT_LEVELS = [
    "Answer directly; no deliberation needed.",
    "A little thought; one obvious approach.",
    "Real thinking; several parts or an approach to choose.",
    "Deep deliberation; tradeoffs, hidden causes or high cost of error.",
]
QUESTIONS = {
    "tier": {
        "type": "choice",
        "instructions": (
            "Which tier of model should answer `prompt`, given `recent_turns` "
            "as context for what the user is doing? Judge the nature of the "
            "work the prompt asks for, not its length."
        ),
        "criteria": TIER_CRITERIA,
    },
    "effort": {
        "type": "score",
        "instructions": "How much deliberation does answering `prompt` well require?",
        "criteria": EFFORT_LEVELS,
    },
    "continuation": {
        "type": "noul",
        "instructions": (
            "`prompt` is a follow-up that depends on the previous turn rather "
            "than a new task: an approval (\"yes\", \"go ahead\"), \"continue\", a "
            "small tweak to what was just produced, or a reaction to it."
        ),
    },
    "stakes": {
        "type": "noul",
        "instructions": (
            "Carrying out `prompt` has real-world consequences if done wrong: "
            "it sends or publishes something, deletes or overwrites data, "
            "deploys, spends money, or produces legal/financial text that "
            "someone will rely on."
        ),
    },
}


def tier_of_model(provider: str, model: str) -> int | None:
    """Degrau do modelo da sessão pela família; None se fora do ladder."""
    m = (model or "").lower()
    for family, idx in FAMILY_TIER.get(provider, ()):
        if family in m:
            return idx
    return None


def build_state(prompt: str, recent: list[str], send_context: bool) -> dict:
    prompt = prompt[:CAP_CHARS]
    if not send_context:
        return {"prompt": prompt, "recent_turns": ["(contexto desligado)"]}
    turns = [r[: CAP_CHARS // 2] for r in recent[-2:]] or ["(início da sessão)"]
    return {"prompt": prompt, "recent_turns": turns}


@dataclass
class Decision:
    tier: int                 # degrau escolhido
    effort: str               # low/medium/high/xhigh
    asked: int                # degrau que o Jev apontou (argmax)
    probs: list               # [leve, padrao, pesado, maximo]
    cont: float
    stakes: float
    tags: list = field(default_factory=list)
    ms: int = 0

    @property
    def tier_name(self) -> str:
        return TIERS[self.tier]

    def line(self) -> str:
        """A linha que o aluno vê em cima da resposta."""
        conf = int(round(self.probs[self.tier] * 100))
        parts = [f"⚙️ {self.tier_name} · esforço {EFFORTS.index(self.effort)} · {conf}%"]
        parts += self.tags
        if self.ms:
            parts.append(f"{self.ms}ms")
        return " · ".join(parts)


def decide(answers: dict, current: int, ms: int = 0) -> Decision:
    """A política. ``current`` = degrau do modelo que rodou no turno anterior."""
    p_raw = answers["tier"]["probabilities"]
    probs = [float(p_raw.get(t, 0.0)) for t in TIERS]
    asked = max(range(4), key=lambda i: probs[i])
    cont = float(answers["continuation"]["noul"])
    stakes = float(answers["stakes"]["noul"])
    score = float(answers["effort"].get("score", 1.0))
    eff_idx = min(max(int(round(score)), 0), 3)
    tags: list[str] = []

    tier = current
    if cont < CONT_HOLD and stakes < STAKES_FLOOR:
        for t in range(current - 1, -1, -1):
            if sum(probs[: t + 1]) >= DOWN_MASS:
                tier = t
            else:
                break
    for t in range(current + 1, 4):
        if sum(probs[t:]) >= UP_MASS:
            tier = t

    if cont >= CONT_HOLD and tier == current:
        tags.append("segue")
    if stakes >= STAKES_FLOOR:
        if tier < current:
            tier = current
        tags.append("risco")
        eff_idx = max(eff_idx, 1)
    if tier == current and asked != current and "segue" not in tags:
        tags.append(f"segurou:{TIERS[asked]}")

    return Decision(tier=tier, effort=EFFORTS[eff_idx], asked=asked, probs=probs,
                    cont=cont, stakes=stakes, tags=tags, ms=ms)
