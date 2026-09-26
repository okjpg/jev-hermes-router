#!/bin/sh
# Instala o jev-hermes-router no Hermes desta máquina.
# Contorna um bug do gerenciador de plugins do Hermes 0.21.x (procura pm/uv.lock na cópia errada).
set -e

HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
DEST="$HOME_DIR/plugins/jev-hermes-router"

if ! command -v hermes >/dev/null 2>&1; then
  echo "hermes não encontrado no PATH. Instala o Hermes primeiro." >&2
  exit 1
fi

# Onde o Hermes realmente mora. O launcher `hermes` costuma rodar de uma cópia gerada
# (installs/<id>/environments/<gen>/workspace), e é nela que o bug do pm/uv.lock aparece.
# O source de verdade tem pm/uv.lock e um venv/ próprio. Procuramos nele.
find_source() {
  for cand in "$HERMES_SOURCE" /usr/local/lib/hermes-agent "$HOME/.hermes/hermes-agent" "$HOME/hermes-agent" /opt/hermes-agent; do
    [ -n "$cand" ] && [ -f "$cand/pm/uv.lock" ] && [ -x "$cand/venv/bin/python" ] && { echo "$cand"; return; }
  done
  # último recurso: sobe a partir do launcher até achar hermes_cli/ com pm/uv.lock
  d="$(dirname "$(readlink -f "$(command -v hermes)")")"
  while [ "$d" != "/" ]; do
    [ -f "$d/pm/uv.lock" ] && [ -x "$d/venv/bin/python" ] && { echo "$d"; return; }
    d="$(dirname "$d")"
  done
}
ROOT="$(find_source)"
if [ -z "$ROOT" ]; then
  echo "não achei o source do Hermes (pasta com pm/uv.lock e venv/). Se instalou em lugar próprio, roda: HERMES_SOURCE=/caminho sh install.sh" >&2
  exit 1
fi
PY="$ROOT/venv/bin/python"

if [ -d "$DEST" ]; then
  echo "→ atualizando $DEST"
  git -C "$DEST" pull -q --ff-only
else
  echo "→ clonando em $DEST"
  git clone -q https://github.com/okjpg/jev-hermes-router "$DEST"
fi

echo "→ ativando"
HERMES_HOME="$HOME_DIR" "$PY" -c "import sys; sys.path.insert(0, '$ROOT'); from hermes_cli.main import main; sys.argv = ['hermes', 'plugins', 'enable', 'jev-hermes-router']; main()" >/dev/null 2>&1 || true

if HERMES_HOME="$HOME_DIR" hermes plugins list --plain --no-bundled 2>/dev/null | grep -q "^enabled .*jev-hermes-router"; then
  echo "✓ jev-hermes-router ativo."
  echo "  Agora abre o Hermes numa sessão nova e roda:  /jev setup"
else
  echo "✗ não ativou. Roda 'hermes plugins doctor jev-hermes-router' e manda a saída." >&2
  exit 1
fi
