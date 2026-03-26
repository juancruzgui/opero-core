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

# Install the package
pip install -e "$SCRIPT_DIR" --quiet 2>&1 | tail -1
echo "  Installed opero-core package"

# Initialize opero in the project
cd "$PROJECT_DIR"
opero

echo ""
echo "✦ Done. Next step:"
echo "  claude"
