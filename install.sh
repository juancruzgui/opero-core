#!/usr/bin/env bash
set -e

# Opero Core installer / updater
# First install:
#   git clone https://github.com/juancruzgui/opero-core.git .opero-core
#   .opero-core/install.sh
#
# Update (after git pull in .opero-core):
#   .opero-core/install.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(pwd)"

# Don't run from inside the opero-core repo itself
if [ "$SCRIPT_DIR" = "$PROJECT_DIR" ]; then
    echo "✦ Error: Run this from your project directory, not from inside opero-core."
    echo ""
    echo "  cd ~/your-project"
    echo "  git clone https://github.com/juancruzgui/opero-core.git .opero-core"
    echo "  .opero-core/install.sh"
    exit 1
fi

VENV_DIR="$PROJECT_DIR/.opero/venv"
IS_UPDATE=false

if [ -d "$VENV_DIR" ] && [ -f "$PROJECT_DIR/.opero/opero.db" ]; then
    IS_UPDATE=true
    echo "✦ Updating Opero Core in: $PROJECT_DIR"
else
    echo "✦ Installing Opero Core into: $PROJECT_DIR"
fi
echo ""

# Create or reuse virtualenv
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "  Created virtualenv: .opero/venv"
fi

# Always reinstall package (picks up code changes)
"$VENV_DIR/bin/pip" install -e "$SCRIPT_DIR" --quiet 2>&1 | tail -1
echo "  Installed opero-core package"

# Create wrapper script
WRAPPER="$PROJECT_DIR/.opero/bin/opero"
mkdir -p "$PROJECT_DIR/.opero/bin"
cat > "$WRAPPER" << 'WRAPPER_EOF'
#!/usr/bin/env bash
OPERO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec "$OPERO_ROOT/.opero/venv/bin/python" -m opero.cli.main "$@"
WRAPPER_EOF
chmod +x "$WRAPPER"

export PATH="$PROJECT_DIR/.opero/bin:$PATH"

if [ "$IS_UPDATE" = true ]; then
    # Update: just rewire Claude Code integration (DB migrates automatically on connect)
    "$WRAPPER" claude setup
    echo ""
    echo "✦ Updated. Restart Claude Code for changes to take effect."
else
    # Fresh install: full init
    cd "$PROJECT_DIR"
    "$WRAPPER" init
    echo ""
    echo "✦ Done."
    echo ""
    echo "  To use opero in this shell:"
    echo "    export PATH=\"$PROJECT_DIR/.opero/bin:\$PATH\""
    echo ""
    echo "  Then start Claude Code:"
    echo "    claude"
fi
