#!/usr/bin/env python3
"""Punto de entrada de los scripts del plugin `baas` (Python 3.10+, solo biblioteca estándar).

Uso: python3 "${CLAUDE_PLUGIN_ROOT}/scripts/baas.py" <orden> ...
Órdenes: set-key, init, status, desktop-config, run-script, sync, inspect, publish, export y setup.
"""

import os
import sys

if sys.version_info < (3, 10):  # noqa: UP036 (se comprueba antes de importar nada que lo exija)
    sys.stderr.write("[BAAS_CONFIG_ERROR] El plugin baas necesita Python 3.10 o superior.\n")
    sys.exit(2)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from baas_plugin.cli import main

if __name__ == "__main__":
    sys.exit(main())
