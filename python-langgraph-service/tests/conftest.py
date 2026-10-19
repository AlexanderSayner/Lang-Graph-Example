import sys
from pathlib import Path

# Make `app` importable regardless of where pytest was invoked from, so tests can just
# `from app.clients.llm import ...` without needing the service root on PYTHONPATH.
SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))
