#!/usr/bin/env bash
set -e

# Opero Core installer
# Run from inside your project:
#   git clone https://github.com/juancruzgui/opero-core.git .opero-core
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

echo "✦ Installing Opero Core into: $PROJECT_DIR"
echo ""

VENV_DIR="$PROJECT_DIR/.opero/venv"

# Create isolated virtualenv for opero
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "  Created virtualenv: .opero/venv"
else
    echo "  Virtualenv exists: .opero/venv"
fi

# Install opero into its own venv
"$VENV_DIR/bin/pip" install -e "$SCRIPT_DIR" --quiet 2>&1 | tail -1
echo "  Installed opero-core package"

# Create a wrapper script so 'opero' works from anywhere in the project
WRAPPER="$PROJECT_DIR/.opero/bin/opero"
mkdir -p "$PROJECT_DIR/.opero/bin"
cat > "$WRAPPER" << 'WRAPPER_EOF'
#!/usr/bin/env bash
OPERO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec "$OPERO_ROOT/.opero/venv/bin/python" -m opero.cli.main "$@"
WRAPPER_EOF
chmod +x "$WRAPPER"
echo "  Created wrapper: .opero/bin/opero"

# Add .opero/bin to PATH for this session and suggest for shell
export PATH="$PROJECT_DIR/.opero/bin:$PATH"

# Initialize opero in the project
cd "$PROJECT_DIR"
"$WRAPPER" init

echo ""
echo "✦ Done."
echo ""
echo "  To use opero in this shell:"
echo "    export PATH=\"$PROJECT_DIR/.opero/bin:\$PATH\""
echo ""
echo "  Or add to your shell profile (~/.zshrc or ~/.bashrc):"
echo "    export PATH=\"\$HOME/$(python3 -c "import os; print(os.path.relpath('$PROJECT_DIR', os.path.expanduser('~')))")/.opero/bin:\$PATH\""
echo ""
echo "  Then start Claude Code:"
echo "    claude"
