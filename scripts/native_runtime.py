"""Write the native bundle's local service paths; never copies clinical data."""
import json
import os
import shutil
import sys
from pathlib import Path
root = Path(os.environ['CLINIC_BUILD_ROOT']).resolve()
port = int(os.environ.get('CLINIC_NATIVE_PORT', '8766'))
database = Path(os.environ.get('CLINIC_NATIVE_DB', str(root / 'data/clinic.sqlite3'))).resolve()
python = os.environ.get('CLINIC_PYTHON') or shutil.which('python3', path='/opt/homebrew/bin:/usr/local/bin:/usr/bin') or sys.executable
output = Path(os.environ['CLINIC_RUNTIME_OUT'])
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(dict(root=str(root), database=str(database), python=python, port=port), ensure_ascii=False))
