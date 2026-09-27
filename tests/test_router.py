"""Testes sem rede e sem Hermes: política, transporte com fetch falso, hooks com contexto falso.

    cd jev-hermes-router && python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE.parent))
PKG = HERE.name.replace("-", "_") if False else HERE.name
plugin = importlib.import_module(f"{PKG}")
pol = importlib.import_module(f"{PKG}.policy")
jev = importlib.import_module(f"{PKG}.jev")
rep = importlib.import_module(f"{PKG}.report")


def answers(probs, cont=0.1, stakes=0.1, effort=1.0):
    return {
        "tier": {"type": "choice", "choice": pol.TIERS[max(range(4), key=lambda i: probs[i])],
                 "confidence": max(probs), "probabilities": dict(zip(pol.TIERS, probs))},
        "effort": {"type": "score", "score": effort, "confidence": 0.7, "probabilities": {}},
        "continuation": {"type": "noul", "noul": cont},
        "stakes": {"type": "noul", "noul": stakes},
    }


class Politica(unittest.TestCase):
    def test_ok_desce_de_pesado_pra_leve(self):
        d = pol.decide(answers([0.95, 0.05, 0, 0], cont=0.45, effort=0), current=2)
        self.assertEqual(d.tier_name, "leve"); self.assertEqual(d.effort, "low")

    def test_manda_bala_fica_no_modelo_do_plano(self):
        d = pol.decide(answers([0.79, 0.16, 0.04, 0.01], cont=0.86), current=2)
        self.assertEqual(d.tier_name, "pesado"); self.assertIn("segue", d.tags)

    def test_desce_um_degrau_so(self):
        # 56% leve + 43% padrao: P(≤padrao)=0.99 ≥ 0.6 desce; P(≤leve)=0.56 < 0.6 para
        d = pol.decide(answers([0.56, 0.43, 0.01, 0.0], cont=0.2), current=3)
        self.assertEqual(d.tier_name, "padrao")

    def test_sobe_pro_maximo(self):
        d = pol.decide(answers([0, 0, 0.16, 0.84], effort=2.4), current=1)
        self.assertEqual(d.tier_name, "maximo"); self.assertEqual(d.effort, "high")

    def test_risco_nao_desce_e_esforco_minimo_1(self):
        d = pol.decide(answers([0.11, 0.61, 0.24, 0.04], cont=0.12, stakes=0.88, effort=0.2), current=2)
        self.assertEqual(d.tier_name, "pesado"); self.assertIn("risco", d.tags); self.assertEqual(d.effort, "medium")

    def test_risco_ainda_sobe(self):
        d = pol.decide(answers([0, 0.1, 0.2, 0.7], stakes=0.9), current=1)
        self.assertEqual(d.tier_name, "maximo")

    def test_segurou_marca_o_pedido(self):
        d = pol.decide(answers([0.5, 0.28, 0.16, 0.06], cont=0.3), current=2)
        self.assertEqual(d.tier_name, "padrao")  # P(≤padrao)=0.78 desce 1; P(≤leve)=0.5 para
        d2 = pol.decide(answers([0.55, 0.03, 0.42, 0.0], cont=0.3), current=2)  # P(≤padrao)=0.58 < 0.6
        self.assertEqual(d2.tier_name, "pesado"); self.assertIn("segurou:leve", d2.tags)

    def test_linha(self):
        d = pol.decide(answers([0, 0.02, 0.89, 0.09], cont=0.82, effort=2), current=2, ms=641)
        self.assertEqual(d.line(), "⚙️ pesado · esforço 2 · 89% · segue · 641ms")

    def test_tier_of_model(self):
        self.assertEqual(pol.tier_of_model("anthropic", "claude-opus-5-5"), 2)
        self.assertEqual(pol.tier_of_model("openai-codex", "gpt-6-luna-900k"), 0)
        self.assertIsNone(pol.tier_of_model("openai-codex", "gpt-5.6-daybreak"))
        self.assertIsNone(pol.tier_of_model("openrouter", "claude-opus-5"))

    def test_state_privacidade(self):
        s = pol.build_state("x" * 5000, ["a" * 3000, "b", "c"], True)
        self.assertEqual(len(s["prompt"]), 2000); self.assertEqual(s["recent_turns"], ["b", "c"])
        self.assertEqual(pol.build_state("oi", ["a"], False)["recent_turns"], ["(contexto desligado)"])


class Transporte(unittest.TestCase):
    def test_fetch_falso_ok(self):
        body = json.dumps({"answers": answers([1, 0, 0, 0]), "model": "jev-x", "usage": {"input_tokens": 5}}).encode()
        r = jev.ask({"prompt": "oi"}, pol.QUESTIONS, key="k", fetch=lambda *a: (200, body))
        self.assertEqual(r.usage["model"], "jev-x"); self.assertEqual(r.usage["input_tokens"], 5)

    def test_http_403_vira_erro_sem_corpo(self):
        with self.assertRaises(jev.JevError) as cm:
            jev.ask({"prompt": "oi"}, pol.QUESTIONS, key="k", fetch=lambda *a: (403, b"segredo"))
        self.assertEqual(cm.exception.motivo(), "http_error 403")

    def test_timeout(self):
        def slow(*a):
            raise TimeoutError
        with self.assertRaises(jev.JevError) as cm:
            jev.ask({"prompt": "oi"}, pol.QUESTIONS, key="k", fetch=slow)
        self.assertEqual(cm.exception.stage, "timeout_or_transport")

    def test_sem_chave(self):
        with self.assertRaises(jev.JevError):
            jev.ask({"prompt": "oi"}, pol.QUESTIONS, key="", fetch=lambda *a: (200, b"{}"))


class FakeCtx:
    def __init__(self):
        self.cfg = {"setup_done": True, "enabled": True, "provider": "anthropic", "route": "typesafe", "send_context": True}
        self.hooks, self.mw, self.cmds = {}, {}, {}

    def get_config(self, k, d=None): return self.cfg.get(k, d)
    def set_config(self, k, v): self.cfg[k] = v
    def register_hook(self, n, f): self.hooks[n] = f
    def register_middleware(self, n, f): self.mw[n] = f
    def register_command(self, n, f, **kw): self.cmds[n] = f


class Hooks(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx(); plugin.register(self.ctx)
        plugin._turn.clear(); plugin._session.clear()
        self.tmp = tempfile.mkdtemp(); plugin._usage_path = lambda: Path(self.tmp) / "usage.jsonl"
        os.environ["TYPESAFE_API_KEY"] = "k"
        self.jev_calls = []

        def fake_ask(state, questions, **kw):
            self.jev_calls.append(state)
            return jev.JevResult(answers=self._answers, usage={"model": "jev-x", "input_tokens": 700}, ms=600)
        self._orig_ask = jev.ask
        jev.ask = fake_ask
        self._answers = answers([0.95, 0.05, 0, 0], cont=0.3, effort=0)

    def tearDown(self):
        jev.ask = self._orig_ask

    def _turn(self, text, turn="t1", session="s1", platform="cli", parent="", provider="anthropic", model="claude-opus-5-5", api_call_count=1):
        if api_call_count == 1:
            self.ctx.hooks["pre_llm_call"](session_id=session, turn_id=turn, user_message=text, platform=platform, parent_session_id=parent, model=model)
        req = {"model": model, "messages": [], "thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}, "max_tokens": 8000}
        return self.ctx.mw["llm_request"](request=req, original_request=dict(req), turn_id=turn, session_id=session, provider=provider, model=model, api_call_count=api_call_count, platform=platform)

    def test_ok_vai_pro_haiku_sem_thinking(self):
        out = self._turn("Ok")
        self.assertEqual(out["request"]["model"], "claude-haiku-4-5")
        self.assertNotIn("thinking", out["request"]); self.assertNotIn("output_config", out["request"])
        self.assertEqual(len(self.jev_calls), 1)

    def test_opus_mantem_effort_adaptativo(self):
        self._answers = answers([0, 0, 0.9, 0.1], cont=0.2, effort=2)
        out = self._turn("faz a auditoria completa")
        self.assertEqual(out["request"]["model"], "claude-opus-5"); self.assertEqual(out["request"]["output_config"]["effort"], "high")

    def test_codex_reasoning_effort(self):
        self._answers = answers([0.9, 0.1, 0, 0], effort=0)
        self.ctx.hooks["pre_llm_call"](session_id="s2", turn_id="t9", user_message="ok", platform="telegram", parent_session_id="", model="gpt-6-sol")
        req = {"model": "gpt-6-sol", "input": [], "reasoning": {"effort": "high", "summary": "auto"}}
        out = self.ctx.mw["llm_request"](request=req, original_request=dict(req), turn_id="t9", session_id="s2", provider="openai-codex", model="gpt-6-sol", api_call_count=1)
        self.assertEqual(out["request"]["model"], "gpt-6-luna"); self.assertEqual(out["request"]["reasoning"], {"effort": "low", "summary": "auto"})

    def test_cron_subagente_kanban_nao_roteiam(self):
        self.assertIsNone(self._turn("ok", turn="c1", platform="cron")); self.assertEqual(self.jev_calls, [])
        self.assertIsNone(self._turn("ok", turn="c2", parent="pai")); self.assertEqual(self.jev_calls, [])
        os.environ["HERMES_KANBAN_TASK"] = "x"
        try:
            self.assertIsNone(self._turn("ok", turn="c3"))
        finally:
            del os.environ["HERMES_KANBAN_TASK"]
        self.assertEqual(self.jev_calls, [])

    def test_provedor_fora_do_ladder_silencia(self):
        out = self._turn("ok", provider="openrouter", model="anthropic/claude-opus-5")
        self.assertIsNone(out)
        line = self.ctx.hooks["transform_llm_output"](response_text="resp", turn_id="t1", session_id="s1")
        self.assertTrue(line.startswith("⚙️ sem rota · provedor openrouter"))

    def test_rotacao_de_provedor_mid_turn_nao_toca(self):
        self._turn("ok")  # decidiu anthropic
        req = {"model": "gpt-6-sol", "input": []}
        out = self.ctx.mw["llm_request"](request=req, original_request=dict(req), turn_id="t1", session_id="s1", provider="openai-codex", model="gpt-6-sol", api_call_count=2)
        self.assertIsNone(out)

    def test_segunda_chamada_do_turno_repete_sem_perguntar(self):
        self._turn("ok"); out = self._turn("ok", api_call_count=2)
        self.assertEqual(out["request"]["model"], "claude-haiku-4-5"); self.assertEqual(len(self.jev_calls), 1)

    def test_sticky_usa_modelo_que_rodou(self):
        self._turn("Ok")  # sessão desce pra haiku
        self._answers = answers([0.6, 0.3, 0.1, 0], cont=0.86)  # continuação: fica no que rodou
        out = self._turn("e agora?", turn="t2")
        self.assertEqual(out["request"]["model"], "claude-haiku-4-5")
        self.assertIn("segue", plugin._turn["t2"]["decision"].tags)
        self.assertEqual(self.jev_calls[-1]["recent_turns"], ["Ok"])

    def test_falha_do_jev_deixa_o_turno_e_avisa(self):
        def boom(*a, **k): raise jev.JevError("http_error", 403)
        jev.ask = boom
        self.assertIsNone(self._turn("ok"))
        line = self.ctx.hooks["transform_llm_output"](response_text="resp", turn_id="t1", session_id="s1")
        self.assertEqual(line, "⚠️ sem rota · http_error 403\n\nresp")

    def test_post_api_grava_jsonl_e_linha(self):
        self._turn("Ok")
        self.ctx.hooks["post_api_request"](turn_id="t1", session_id="s1", provider="anthropic", model="claude-haiku-4-5", response_model="claude-haiku-4-5-20251001",
                                           usage={"prompt_tokens": 1000, "cache_read_tokens": 900, "completion_tokens": 10})
        rows = [json.loads(l) for l in plugin._usage_path().read_text().splitlines()]
        self.assertEqual(rows[0]["tier"], "leve"); self.assertEqual(rows[0]["cache_read"], 900); self.assertEqual(rows[0]["session_model"], "claude-opus-5-5")
        line = self.ctx.hooks["transform_llm_output"](response_text="resp", turn_id="t1", session_id="s1")
        self.assertTrue(line.startswith("⚙️ leve · esforço 0 · 95% · 600ms\n\nresp"), line)
        self.assertIn("leve 1", plugin._stats())

    def test_padrao_codex_linha_e_log_mostram_esforco_aplicado(self):
        self._answers = answers([0.1, 0.8, 0.1, 0], effort=0)
        self.ctx.hooks["pre_llm_call"](session_id="s3", turn_id="t3", user_message="explica idempotência", platform="desktop", parent_session_id="", model="gpt-6-sol")
        req = {"model": "gpt-6-sol", "input": [], "reasoning": {"effort": "high"}}
        out = self.ctx.mw["llm_request"](request=req, original_request=dict(req), turn_id="t3", session_id="s3", provider="openai-codex", model="gpt-6-sol", api_call_count=1)
        self.assertEqual(out["request"]["model"], "gpt-6-luna"); self.assertEqual(out["request"]["reasoning"]["effort"], "high")
        self.ctx.hooks["post_api_request"](turn_id="t3", session_id="s3", provider="openai-codex", model="gpt-6-luna", response_model="gpt-6-luna",
                                           api_call_count=1, usage={"prompt_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 50, "reasoning_tokens": 20})
        row = json.loads(plugin._usage_path().read_text().splitlines()[-1])
        self.assertEqual(row["effort"], "high"); self.assertEqual(row["jev_effort"], "low")
        self.assertEqual(row["completion_tokens"], 50); self.assertEqual(row["call"], 1); self.assertEqual(row["session"], "s3")
        line = self.ctx.hooks["transform_llm_output"](response_text="r", turn_id="t3", session_id="s3")
        self.assertTrue(line.startswith("⚙️ padrao · esforço 2 ·"), line)

    def test_relatorio_pelo_comando(self):
        self._turn("Ok")
        self.ctx.hooks["post_api_request"](turn_id="t1", session_id="s1", provider="anthropic", model="claude-haiku-4-5", api_call_count=1,
                                           usage={"prompt_tokens": 20000, "cache_read_tokens": 0, "output_tokens": 100})
        plugin._data_dir = lambda: Path(self.tmp)
        orig = rep.relatorio
        seen = {}
        rep.relatorio = lambda root, janela="24h", *a, **k: seen.setdefault("args", (root, janela)) and "ok"
        try:
            self.assertEqual(self.ctx.cmds["jev"]("relatorio semana"), "ok")
            self.assertEqual(seen["args"][1], "7d")
            self.assertIn("Uso:", self.ctx.cmds["jev"]("relatorio ontem"))
        finally:
            rep.relatorio = orig

    def test_quieto_e_desligado(self):
        self.ctx.cfg["quiet"] = True; self._turn("Ok")
        self.assertIsNone(self.ctx.hooks["transform_llm_output"](response_text="r", turn_id="t1", session_id="s1"))
        self.ctx.cfg["enabled"] = False; self.assertIsNone(self._turn("ok", turn="t3")); self.assertEqual(len(self.jev_calls), 1)

    def test_wizard_fluxo(self):
        self.ctx.cfg = {}; c = self.ctx.cmds["jev"]
        self.assertIn("Ainda não configurado", c(""))
        self.assertIn("1/3", c("setup")); self.assertIn("2/3", c("setup 1")); self.assertEqual(self.ctx.cfg["provider"], "openai-codex")
        os.environ.pop("TYPESAFE_API_KEY", None)
        self.assertIn("Cola a chave", c("setup 1"))
        saved = {}
        import types
        fake_cfg = types.ModuleType("hermes_cli.config"); fake_cfg.save_env_value = lambda k, v: saved.__setitem__(k, v)
        sys.modules["hermes_cli.config"] = fake_cfg
        try:
            self.assertIn("3/3", c("chave apikey_teste"))
        finally:
            del sys.modules["hermes_cli.config"]
        self.assertEqual(saved, {"TYPESAFE_API_KEY": "apikey_teste"})
        fim = c("setup 1")
        self.assertIn("Pronto. Como funciona", fim); self.assertIn("Luna → Luna+ → Sol → Astra", fim); self.assertNotIn("Haiku", fim)
        self.assertTrue(self.ctx.cfg["setup_done"]); self.assertTrue(self.ctx.cfg["send_context"])
        self.assertIn("Setup já feito", c("setup 1"))


def _row(ts, sess, tier, model, session_model, pt, cr, out, call=1, provider="openai-codex"):
    return {"ts": ts, "session": sess, "turn": f"{sess}-{ts}", "call": call, "provider": provider, "session_model": session_model,
            "model": model, "applied": model, "tier": tier, "prompt_tokens": pt, "cache_read": cr, "completion_tokens": out,
            "jev_ms": 300, "jev_tokens": 750}


class Relatorio(unittest.TestCase):
    def _home(self, rows):
        home = Path(tempfile.mkdtemp())
        d = home / "plugin-data" / "jev-hermes-router"; d.mkdir(parents=True)
        (d / "usage.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        return home

    def test_descida_economiza_e_conta_turnos(self):
        now = 1_000_000
        rows = [_row(now - 60, "a", "leve", "gpt-6-luna", "gpt-6-sol", 20000, 0, 100),
                _row(now - 50, "a", "leve", "gpt-6-luna", "gpt-6-sol", 21000, 20000, 50, call=2)]
        s = rep.resumir(rep.load(self._home(rows) / "plugin-data/jev-hermes-router/usage.jsonl", None))
        self.assertEqual((s.turnos, s.chamadas, s.desceu[0]), (1, 2, 2))
        self.assertGreater(s.economia, 0.8)

    def test_subida_aparece_como_gasto_extra(self):
        rows = [_row(10, "b", "maximo", "gpt-6-astra", "gpt-6-sol", 30000, 0, 2000)]
        s = rep.resumir(rows)
        self.assertLess(s.economia, 0); self.assertEqual(s.subiu[0], 1)
        self.assertIn("Gasto extra", rep.formatar(s, "24h"))

    def test_troca_cobra_cache_frio(self):
        # mesma sessão: 2ª chamada troca de modelo; o "sem jev" teria o cache quente
        rows = [_row(1, "c", "pesado", "gpt-6-sol", "gpt-6-sol", 50000, 0, 100),
                _row(2, "c", "leve", "gpt-6-luna", "gpt-6-sol", 50500, 0, 100)]
        s = rep.resumir(rows)
        self.assertEqual(s.trocas, 1)
        base_2a = rep._cost(rep.PRICES["sol"], 500, 50000, 100)
        self.assertAlmostEqual(s.desceu[2], base_2a, places=6)

    def test_janela_e_vazio_falam(self):
        home = self._home([_row(100, "a", "leve", "gpt-6-luna", "gpt-6-sol", 1000, 0, 10)])
        self.assertIn("Nenhuma mensagem", rep.relatorio(home, "24h", now=100 + 2 * 86400))
        self.assertIn("1 mensagens", rep.relatorio(home, "tudo"))

    def test_linha_antiga_sem_call_nem_saida(self):
        rows = [dict(_row(1, "a", "leve", "gpt-6-luna", "gpt-6-sol", 1000, 0, None)), dict(_row(2, "a", "leve", "gpt-6-luna", "gpt-6-sol", 1200, 1000, None))]
        for r in rows:
            r.pop("call")
        s = rep.resumir(rows)
        self.assertEqual((s.turnos, s.sem_saida), (1, 2))


if __name__ == "__main__":
    unittest.main()
