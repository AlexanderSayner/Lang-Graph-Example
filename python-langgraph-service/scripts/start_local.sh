#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Starting local setup..."

# 1. Create venv if not exists
if [ ! -d "${PROJECT_ROOT}/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "${PROJECT_ROOT}/venv"
fi

# 2. Activate venv
source "${PROJECT_ROOT}/venv/bin/activate"

# 3. Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r "${PROJECT_ROOT}/requirements.txt"

# 4. Generate Proto files
echo "Generating proto files..."
bash "${SCRIPT_DIR}/generate_proto.sh"

# 5. Run server
echo "Starting server..."
cd "${PROJECT_ROOT}"
python -m app.main