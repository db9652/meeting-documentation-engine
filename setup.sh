#!/usr/bin/env bash
# ==============================================================================
# Setup Script for Multimodal Meeting Documentation Engine
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "======================================================================"
echo "🛠️  Setting up Multimodal Meeting Documentation Environment"
echo "======================================================================"

# 1. Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 could not be found. Please install Python 3.9+."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✅ Detected Python $PYTHON_VERSION"

# 2. Setup Virtual Environment
VENV_DIR="$SCRIPT_DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment in ./venv..."
    python3 -m venv "$VENV_DIR"
else
    echo "📦 Existing virtual environment found at ./venv"
fi

# 3. Upgrade pip and install dependencies
echo "📥 Installing dependencies from requirements.txt..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

# 4. Make scripts executable
chmod +x "$SCRIPT_DIR/process_meeting.py"

echo ""
echo "======================================================================"
echo "🎉 Setup Complete!"
echo "======================================================================"
echo "To process a meeting recording, run:"
echo ""
echo "  ./venv/bin/python3 process_meeting.py \\"
echo "      --video sample/sample_meeting.mp4 \\"
echo "      --vtt sample/sample_meeting.en.vtt \\"
echo "      --output-dir ./output \\"
echo "      --title \"Your Meeting Title\""
echo ""
echo "Or activate the virtual environment first:"
echo "  source venv/bin/activate"
echo "  python3 process_meeting.py --help"
echo "======================================================================"
