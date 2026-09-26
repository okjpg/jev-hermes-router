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

# Onde o Hermes realmente mora (o launcher é um script Python; a raiz é o pai de hermes_cli/)
LAUNCHER="$(readlink -f "$(command -v hermes)")"
ROOT="$(dirname "$LAUNCHER")"
while [ "$ROOT" != "/" ] && [ ! -d "$ROOT/hermes_cli" ]; do ROOT="$(dirname "$ROOT")"; done
if [ ! -d "$ROOT/hermes_cli" ]; then
  ROOT="$(hermes --version 2>/dev/null | sed -n 's/.*(\(\/.*\)).*/\1/p' | head -1)"
fi
PY="$ROOT/venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "não achei o Python do Hermes em $ROOT/venv/bin/python" >&2
  exit 1
fi

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
