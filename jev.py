"""Transporte do Jev (TypeSafe System One): direto ou via OpenRouter.

Puro: recebe ``fetch`` e ``now`` por parâmetro para que os testes rodem sem
rede. Nunca expõe corpo de resposta em erro; só um código de estágio.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

USER_AGENT = "jev-hermes-router/0.2 (+hermes plugin)"
MODEL = "jev-latest"
QUESTION_TYPES = {"noul", "choice", "score"}

ROUTES = {
    "typesafe": "https://api.typesafe.ai/v1/systemone",
    "openrouter": "https://openrouter.ai/api/alpha/decisions",
}


class JevError(RuntimeError):
    """Falha sanitizada: estágio + status HTTP, nunca o corpo."""

    def __init__(self, stage: str, status: int | None = None):
        super().__init__(stage)
        self.stage, self.status = stage, status

    def motivo(self) -> str:
        return f"{self.stage} {self.status}".strip() if self.status else self.stage


@dataclass
class JevResult:
    answers: dict
    usage: dict
    ms: int


def _default_fetch(url: str, body: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def _validate_questions(questions: dict) -> None:
    if not isinstance(questions, dict) or not questions:
        raise JevError("invalid_questions")
    for q in questions.values():
        if not isinstance(q, dict) or q.get("type") not in QUESTION_TYPES or not q.get("instructions"):
            raise JevError("invalid_questions")


def _validate_answers(answers: Any, questions: dict) -> None:
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise JevError("answer_key_mismatch")
    for qid, a in answers.items():
        t = questions[qid]["type"]
        if not isinstance(a, dict) or a.get("type") != t:
            raise JevError("answer_type_mismatch")
        if t == "noul" and not (isinstance(a.get("noul"), (int, float)) and 0 <= a["noul"] <= 1):
            raise JevError("answer_invalid")
        if t in {"choice", "score"} and not isinstance(a.get("probabilities"), dict):
            raise JevError("answer_invalid")


def ask(
    state: Any, questions: dict, *, key: str, route: str = "typesafe", timeout: float = 1.5,
    fetch: Callable[..., tuple[int, bytes]] = _default_fetch, model: str = MODEL,
) -> JevResult:
    """Uma chamada, várias perguntas independentes sobre o mesmo ``state``.

    Sem retry: o orçamento é o timeout. Qualquer falha vira ``JevError`` e o
    chamador decide não rotear.
    """
    _validate_questions(questions)
    if route not in ROUTES:
        raise JevError("unknown_route")
    if not key:
        raise JevError("missing_key")
    body = json.dumps({"state": state, "model": model, "questions": questions}).encode()
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": USER_AGENT}
    t0 = time.perf_counter()
    try:
        status, raw = fetch(ROUTES[route], body, headers, timeout)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise JevError("timeout_or_transport") from None
    ms = round((time.perf_counter() - t0) * 1000)
    if status != 200:
        raise JevError("http_error", status)
    try:
        data = json.loads(raw)
    except ValueError:
        raise JevError("invalid_json") from None
    if not isinstance(data, dict):
        raise JevError("answer_invalid")
    _validate_answers(data.get("answers"), questions)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return JevResult(answers=data["answers"], usage={"model": data.get("model"), **usage}, ms=ms)
