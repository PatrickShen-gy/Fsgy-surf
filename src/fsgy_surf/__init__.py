"""Fsgy-surf calculation package and numbered-stage module discovery."""
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parent.parent
PROJECT = SOURCE.parent
for directory in sorted(SOURCE.glob('[0-9][0-9]_*')):
    sys.path.insert(0, str(directory))
# Optional workspace-local dependencies; normal pip installations work as well.
local_dependencies = PROJECT.parent / '.cad_deps'
if local_dependencies.is_dir():
    sys.path.append(str(local_dependencies))

__version__ = '0.1.0'
