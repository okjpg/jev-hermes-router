"""jev-hermes-router: escolhe o modelo certo pra cada mensagem, dentro do Hermes.

Hooks usados (Hermes ≥ 0.21.4):
  pre_llm_call            pega o texto do aluno e pergunta ao Jev (4 perguntas, 1 chamada)
  llm_request (middleware) troca model/effort no request do provedor, sem sair do provedor
  post_api_request        registra o modelo que rodou de verdade + tokens + cache
  transform_llm_output    põe a linha ⚙️ em cima da resposta
  /jev                    setup · status · doctor · off/on · quieto · contexto

Falha aberta: qualquer erro do Jev ou do plugin deixa o turno como estava.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import jev as jevmod
from . import policy as pol

logger = logging.getLogger("jev-hermes-router")
_lock = threading.Lock()
# turn_id → decisão (para o middleware e a linha); session_id → estado da sessão
_turn: dict[str, dict] = {}
_session: dict[str, dict] = {}
_ctx: Any = None
_PLATFORMS_HUMANOS = {"cli", "desktop", "telegram", "whatsapp", "discord", "slack", "hermes_browser", "browser", "dashboard", "api_server", "desktop-ssh", ""}
_MAX_TURNS = 400


# ── config ────────────────────────────────────────────────────────────────────

def _cfg(key: str, default=None):
    try:
        v = _ctx.get_config(key, default)
        return default if v is None else v
    except Exception:
        return default


def _set(key: str, value) -> None:
    try:
        _ctx.set_config(key, value)
    except Exception as exc:
        logger.warning("jev-router: não consegui salvar %s: %s", key, exc)


def _api_key() -> tuple[str, str]:
    """(rota, chave). Chave vem do .env do Hermes (TYPESAFE_API_KEY ou OPENROUTER_API_KEY)."""
    route = str(_cfg("route", "typesafe"))
    env = "TYPESAFE_API_KEY" if route == "typesafe" else "OPENROUTER_API_KEY"
    return route, (os.environ.get(env) or "").strip()


def _data_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir
    return plugin_data_dir("jev-hermes-router")


def _usage_path() -> Path:
    return _data_dir() / "usage.jsonl"


# ── estado por sessão ────────────────────────────────────────────────────────

def _sess(session_id: str) -> dict:
    with _lock:
        return _session.setdefault(session_id, {"recent": [], "current": None, "last": None})


def _remember_turn(turn_id: str, data: dict) -> None:
    with _lock:
        _turn[turn_id] = data
        if len(_turn) > _MAX_TURNS:
            for k in list(_turn)[: len(_turn) - _MAX_TURNS]:
                _turn.pop(k, None)


# ── hooks ─────────────────────────────────────────────────────────────────────

def _is_human_turn(platform: str, parent_session_id: str) -> bool:
    if parent_session_id:
        return False                      # subagente
    if os.environ.get("HERMES_KANBAN_TASK"):
        return False                      # worker de kanban
    return (platform or "") in _PLATFORMS_HUMANOS   # cron vem como "cron"


def _user_text(user_message: Any) -> str:
    if isinstance(user_message, str):
        return user_message
    if isinstance(user_message, list):
        return " ".join(p.get("text", "") for p in user_message if isinstance(p, dict) and p.get("type") == "text")
    return str(user_message or "")


def on_pre_llm_call(**kw) -> None:
    """Uma chamada ao Jev por turno. Guarda a decisão pro middleware; nunca injeta contexto."""
    if not _cfg("enabled", True) or not _cfg("setup_done", False):
        return None
    platform = str(kw.get("platform") or "")
    if not _is_human_turn(platform, str(kw.get("parent_session_id") or "")):
        return None
    session_id, turn_id = str(kw.get("session_id") or ""), str(kw.get("turn_id") or "")
    text = _user_text(kw.get("user_message")).strip()
    if not text or text.startswith("/") or not session_id or not turn_id:
        return None
    provider = str(kw.get("provider") or "")  # nem sempre vem; o middleware confirma
    s = _sess(session_id)
    state = pol.build_state(text, s["recent"], bool(_cfg("send_context", True)))
    route, key = _api_key()
    result: dict = {"text": text[:80], "platform": platform}
    try:
        r = jevmod.ask(state, pol.QUESTIONS, key=key, route=route, timeout=float(_cfg("timeout_s", 1.5)))
        result["answers"], result["ms"], result["jev_tokens"] = r.answers, r.ms, r.usage.get("input_tokens")
    except jevmod.JevError as exc:
        result["error"] = exc.motivo()
    except Exception as exc:  # nunca derruba o turno
        result["error"] = f"plugin: {type(exc).__name__}"
    s["recent"] = (s["recent"] + [text])[-2:]
    _remember_turn(turn_id, result)
    logger.debug("jev-router pre_llm_call turn=%s platform=%s ms=%s err=%s text=%r", turn_id[-8:], platform, result.get("ms"), result.get("error"), text[:40])
    return None


def on_llm_request(**kw) -> Optional[dict]:
    """Troca modelo e esforço no request, só dentro do provedor da sessão."""
    turn_id = str(kw.get("turn_id") or "")
    data = _turn.get(turn_id)
    if not data or "answers" not in data:
        return None
    if int(kw.get("api_call_count") or 1) > 1 and "decision" in data:
        # chamadas seguintes do mesmo turno (loop de tools): repete a decisão, não pergunta de novo
        return _apply(kw, data["decision"], data)
    provider = str(kw.get("provider") or "")
    if provider not in pol.LADDERS:
        data["skip"] = f"provedor {provider or '?'} fora do ladder"
        return None
    request = kw.get("request") or {}
    model_now = str(request.get("model") or kw.get("model") or "")
    s = _sess(str(kw.get("session_id") or ""))
    current = s["current"] if s["current"] is not None else pol.tier_of_model(provider, model_now)
    if current is None:
        data["skip"] = f"modelo {model_now} fora do ladder"
        return None
    d = pol.decide(data["answers"], current, ms=int(data.get("ms") or 0))
    data["decision"], data["provider"], data["session_model"] = d, provider, model_now
    s["current"], s["last"] = d.tier, d
    return _apply(kw, d, data)


def _apply(kw: dict, d: pol.Decision, data: dict) -> Optional[dict]:
    request = dict(kw.get("request") or {})
    provider = data.get("provider") or ""
    if str(kw.get("provider") or "") != provider:
        return None  # rotação/fallback mudou o provedor no meio do turno: não toca
    target = pol.LADDERS[provider][d.tier]
    request["model"] = target
    if provider == "openai-codex":
        request["reasoning"] = {**(request.get("reasoning") or {}), "effort": d.effort}
    elif provider == "anthropic":
        _apply_anthropic_effort(request, target, d.effort)
    data["applied_model"] = target
    return {"request": request, "source": "jev-hermes-router", "reason": f"{d.tier_name}/{d.effort}"}


def _apply_anthropic_effort(request: dict, model: str, effort: str) -> None:
    """Segue o contrato do adapter: adaptativo usa output_config.effort; haiku não pensa.
    max_tokens é clampado ao teto do modelo alvo (opus manda 128k; haiku aceita 64k)."""
    try:
        from agent.anthropic_adapter import _get_anthropic_max_output
        cap = _get_anthropic_max_output(model)
    except Exception:
        cap = 64_000
    if isinstance(request.get("max_tokens"), int) and request["max_tokens"] > cap:
        request["max_tokens"] = cap
    if "haiku" in model:
        request.pop("thinking", None)
        request.pop("output_config", None)
        return
    if isinstance(request.get("thinking"), dict) and request["thinking"].get("type") == "adaptive":
        oc = dict(request.get("output_config") or {})
        oc["effort"] = effort
        request["output_config"] = oc
    elif isinstance(request.get("thinking"), dict) and "budget_tokens" in request["thinking"]:
        budget = {"low": 4000, "medium": 8000, "high": 16000, "xhigh": 32000}[effort]
        request["thinking"] = {"type": "enabled", "budget_tokens": budget}
        request["max_tokens"] = min(max(int(request.get("max_tokens") or 0), budget + 4096), cap)


def on_post_api_request(**kw) -> None:
    """O que rodou de verdade. Fonte do /jev e do recibo de economia."""
    turn_id = str(kw.get("turn_id") or "")
    data = _turn.get(turn_id)
    if not data:
        return None
    usage = kw.get("usage") or {}
    row = {
        "ts": round(time.time()), "session": str(kw.get("session_id") or "")[:12], "turn": turn_id[:12],
        "provider": kw.get("provider"), "session_model": data.get("session_model"),
        "model": kw.get("response_model") or kw.get("model"), "applied": data.get("applied_model"),
        "tier": data["decision"].tier_name if "decision" in data else None,
        "effort": data["decision"].effort if "decision" in data else None,
        "asked": pol.TIERS[data["decision"].asked] if "decision" in data else None,
        "tags": data["decision"].tags if "decision" in data else [],
        "prompt_tokens": usage.get("prompt_tokens"), "cache_read": usage.get("cache_read_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "jev_ms": data.get("ms"), "jev_tokens": data.get("jev_tokens"), "error": data.get("error"), "skip": data.get("skip"),
    }
    data["api"] = row
    try:
        with _usage_path().open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("jev-router usage write failed: %s", exc)
    return None


def on_transform_llm_output(**kw) -> Optional[str]:
    if _cfg("quiet", False):
        return None
    data = _turn.get(str(kw.get("turn_id") or ""))
    if not data:
        return None
    text = kw.get("response_text") or ""
    if text.lstrip().startswith(("⚙️", "⚠️ sem rota")):
        return None  # já prefixado (o runtime pode chamar mais de uma vez por turno)
    if data.get("lined"):
        return None  # uma linha por turno, mesmo que o texto mude entre chamadas
    prefix = None
    if "decision" in data:
        d: pol.Decision = data["decision"]
        prefix = d.line()
        api = data.get("api") or {}
        if api.get("model") and api.get("applied") and not str(api["model"]).startswith(str(api["applied"])):
            prefix += f" · api {api['model']}"
    elif data.get("error"):
        prefix = f"⚠️ sem rota · {data['error']}"
    elif data.get("skip"):
        prefix = f"⚙️ sem rota · {data['skip']}"
    if prefix is None:
        return None
    data["lined"] = True
    logger.debug("jev-router transform turn=%s prefix=%r", str(kw.get("turn_id") or "")[-8:], prefix)
    return f"{prefix}\n\n{text}"


# ── /jev ──────────────────────────────────────────────────────────────────────

def _stats(n: int = 200) -> str:
    p = _usage_path()
    if not p.exists():
        return "Ainda não roteei nenhuma mensagem."
    rows = [json.loads(l) for l in p.read_text().splitlines()[-n:] if l.strip()]
    routed = [r for r in rows if r.get("tier")]
    if not routed:
        return f"{len(rows)} turnos registrados, nenhum roteado (erro ou fora do ladder)."
    by = {}
    for r in routed:
        by[r["tier"]] = by.get(r["tier"], 0) + 1
    dist = " · ".join(f"{t} {by.get(t, 0)}" for t in pol.TIERS)
    pt = [r for r in routed if r.get("prompt_tokens")]
    cache = sum(r.get("cache_read") or 0 for r in pt); tot = sum(r["prompt_tokens"] for r in pt)
    cache_pct = f"{100 * cache / tot:.0f}%" if tot else "—"
    trocas = sum(1 for a, b in zip(routed, routed[1:]) if a["session"] == b["session"] and a["tier"] != b["tier"])
    # degraus abaixo do modelo da sessão = proxy de economia de cota
    saved = 0
    for r in routed:
        st = pol.tier_of_model(r.get("provider") or "", r.get("session_model") or "")
        if st is not None:
            saved += max(st - pol.TIERS.index(r["tier"]), 0)
    ms = [r["jev_ms"] for r in routed if r.get("jev_ms")]
    return (f"Últimos {len(routed)} turnos roteados: {dist}\n"
            f"Trocas de modelo: {trocas} · cache lido: {cache_pct} · degraus abaixo do modelo da sessão: {saved}\n"
            f"Jev: {sum(ms) // max(len(ms), 1)} ms em média")


def _historico(session_id: str) -> str:
    s = _session.get(session_id) or {}
    d = s.get("last")
    if not d:
        return "Nenhum turno roteado nesta sessão ainda."
    return f"Último: {d.line()} · pediu {pol.TIERS[d.asked]} · cont {d.cont:.2f} · risco {d.stakes:.2f}"


def _doctor() -> str:
    out = []
    try:
        from hermes_cli.plugins import VALID_MIDDLEWARE  # noqa
        out.append("✓ runtime com middleware")
    except Exception:
        out.append("✗ runtime sem middleware: roda `hermes update`")
    route, key = _api_key()
    out.append(f"rota do Jev: {route}")
    if not key:
        env = "TYPESAFE_API_KEY" if route == "typesafe" else "OPENROUTER_API_KEY"
        out.append(f"✗ chave ausente: coloque {env} no ~/.hermes/.env ou rode /jev chave <valor>")
    else:
        try:
            r = jevmod.ask({"prompt": "oi", "recent_turns": ["(teste)"]}, pol.QUESTIONS, key=key, route=route, timeout=5)
            out.append(f"✓ Jev respondeu em {r.ms} ms ({r.usage.get('model')})")
        except jevmod.JevError as exc:
            out.append(f"✗ Jev falhou: {exc.motivo()}")
    out.append(f"provedor configurado: {_cfg('provider', 'não definido')} · contexto: {'2 msgs' if _cfg('send_context', True) else 'só prompt'}")
    out.append(f"router: {'ligado' if _cfg('enabled', True) else 'desligado'} · setup: {'feito' if _cfg('setup_done') else 'pendente (/jev setup)'}")
    return "\n".join(out)


_WIZARD = {
    "provider": (
        "1/3 · Qual provedor você usa no Hermes?\n\n"
        "  1. Codex (login da sua conta ChatGPT)\n"
        "  2. Anthropic (login da sua conta Claude)\n"
        "  3. Os dois (uso os dois, depende da sessão)\n\n"
        "O router funciona só com login por assinatura (OAuth). Em OpenRouter ou chave de API ele fica desligado.\n"
        "Responde: /jev setup 1  (ou 2, ou 3)"
    ),
    "route": (
        "2/3 · Onde o Jev vai rodar?\n\n"
        "Isso é só pro Jev (o que escolhe o modelo). Seu ChatGPT/Claude continua no login de sempre.\n\n"
        "  1. TypeSafe direto (recomendado): conta em console.typesafe.ai, US$ 5 de crédito grátis, ~0,6 s\n"
        "  2. OpenRouter: usa o crédito que você já tem lá; rota /api/alpha, um salto a mais\n\n"
        "Responde: /jev setup 1  (ou 2)"
    ),
    "key": (
        "Cola a chave: /jev chave <valor>\n\n"
        "TypeSafe: console.typesafe.ai/keys (começa com apikey_). OpenRouter: openrouter.ai/settings/keys (sk-or-).\n"
        "Fica em ~/.hermes/.env, nunca em log nem no Git. Se já tem OPENROUTER_API_KEY no Hermes: /jev chave mesma"
    ),
    "context": (
        "3/3 · Mandar as 2 mensagens anteriores junto?\n\n"
        "O Jev decide melhor vendo as 2 últimas coisas que VOCÊ escreveu. Sai da sua máquina só texto que você digitou; "
        "nunca resposta do agente, resultado de ferramenta ou arquivo.\n\n"
        "  1. Sim, manda as 2 anteriores (recomendado)\n"
        "  2. Só a mensagem atual\n\n"
        "Responde: /jev setup 1  (ou 2)"
    ),
}


def _explicacao() -> str:
    prov = _cfg("provider", "ambos")
    ladders = []
    if prov in ("openai-codex", "ambos"):
        ladders.append("Sessão no ChatGPT: Luna → Terra → Sol → Astra")
    if prov in ("anthropic", "ambos"):
        ladders.append("Sessão no Claude:  Haiku → Sonnet → Opus → Fable")
    return (
        "Pronto. Como funciona:\n\n"
        "• 4 níveis: leve → padrão → pesado → máximo.\n  " + "\n  ".join(ladders) + "\n"
        "• A cada mensagem, o Jev escolhe nível e quanto o modelo deve pensar.\n"
        "• Uma linha em cima de cada resposta mostra a escolha:  ⚙️ pesado · esforço 2 · 88%\n"
        "• Um \"manda bala\" fica no modelo que fez o plano. Tarefa arriscada (enviar, apagar, publicar) nunca desce.\n"
        "• /jev mostra histórico e economia. /jev off desliga. /jev quieto some com a linha.\n\n"
        "Manda um \"oi\" pra ver funcionando."
    )


def _setup(arg: str) -> str:
    step = str(_cfg("setup_step", "provider"))
    if not arg:
        _set("setup_step", "provider"); _set("setup_done", False)
        return ("⚙️ jev-hermes-router\n\nSeu Hermes manda toda mensagem pro mesmo modelo, mesmo quando é só um \"ok\". "
                "Este plugin escolhe, a cada mensagem, o modelo certo: leve pro trivial, máximo pro que importa. "
                "Quem escolhe é o Jev (TypeSafe), que não escreve nada, só aponta o dedo.\n\n" + _WIZARD["provider"])
    if step == "provider":
        m = {"1": "openai-codex", "2": "anthropic", "3": "ambos"}
        if arg not in m:
            return _WIZARD["provider"]
        _set("provider", m[arg]); _set("setup_step", "route")
        return _WIZARD["route"]
    if step == "route":
        m = {"1": "typesafe", "2": "openrouter"}
        if arg not in m:
            return _WIZARD["route"]
        _set("route", m[arg]); _set("setup_step", "key")
        _, key = _api_key()
        if key:
            return _chave("mesma")
        return _WIZARD["key"]
    if step == "key":
        return _WIZARD["key"]
    if step == "context":
        if arg not in ("1", "2"):
            return _WIZARD["context"]
        _set("send_context", arg == "1"); _set("setup_step", "done"); _set("setup_done", True); _set("enabled", True)
        return _explicacao()
    return "Setup já feito. /jev setup pra refazer."


def _chave(arg: str) -> str:
    route, existing = _api_key()
    env = "TYPESAFE_API_KEY" if route == "typesafe" else "OPENROUTER_API_KEY"
    if arg == "mesma":
        if not existing:
            return f"Não achei {env} no ambiente. Cola: /jev chave <valor>"
        key = existing
    else:
        key = arg.strip()
        if not key:
            return _WIZARD["key"]
    try:
        r = jevmod.ask({"prompt": "oi", "recent_turns": ["(teste)"]}, pol.QUESTIONS, key=key, route=route, timeout=8)
    except jevmod.JevError as exc:
        return f"✗ Jev não respondeu com essa chave ({exc.motivo()}). Confere e cola de novo: /jev chave <valor>"
    if arg != "mesma":
        try:
            from hermes_cli.config import save_env_value
            save_env_value(env, key)
        except Exception as exc:
            return f"Jev respondeu, mas não consegui salvar em .env: {exc}. Coloque {env}=… em ~/.hermes/.env"
        os.environ[env] = key
    if _cfg("setup_step") == "key":
        _set("setup_step", "context")
        return f"✓ Jev respondeu em {r.ms} ms ({r.usage.get('model')}). Chave salva.\n\n" + _WIZARD["context"]
    return f"✓ Jev respondeu em {r.ms} ms. Chave salva."


def jev_command(raw: str = "", **kw) -> str:
    parts = (raw or "").strip().split(None, 1)
    sub = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    if sub == "setup":
        return _setup(arg)
    if sub == "chave":
        return _chave(arg)
    if sub == "doctor":
        return _doctor()
    if sub in ("off", "desliga"):
        _set("enabled", False); return "Router desligado. /jev on liga de novo."
    if sub in ("on", "liga"):
        _set("enabled", True); return "Router ligado."
    if sub == "quieto":
        q = not _cfg("quiet", False); _set("quiet", q)
        return "Linha escondida." if q else "Linha de volta."
    if sub == "contexto":
        on = arg not in ("off", "0", "nao", "não")
        _set("send_context", on); return "Mandando as 2 mensagens anteriores." if on else "Só a mensagem atual vai pro Jev."
    if sub == "stats":
        return _stats()
    if not _cfg("setup_done", False):
        return "Ainda não configurado. Roda /jev setup"
    status = "ligado" if _cfg("enabled", True) else "desligado"
    sid = str(kw.get("session_id") or "")
    hist = _historico(sid) if sid else ""
    return f"jev-hermes-router {status} · provedor {_cfg('provider')} · Jev via {_cfg('route')}\n{hist}\n\n{_stats()}\n\nComandos: setup · doctor · stats · off/on · quieto · contexto off/on"


# ── registro ──────────────────────────────────────────────────────────────────

def register(ctx) -> None:
    global _ctx
    _ctx = ctx
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_middleware("llm_request", on_llm_request)
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("transform_llm_output", on_transform_llm_output)
    ctx.register_command("jev", jev_command, description="Router de modelo por mensagem (Jev)", args_hint="[setup|doctor|stats|off|on|quieto|contexto]")
