"""Run the HA-independent tests without pytest or Home Assistant installed.

The package ``__init__`` imports Home Assistant, so it is bypassed by
registering the package modules by hand. In CI, pytest runs everything.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
import traceback
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for name, path in (
    ("custom_components", ROOT / "custom_components"),
    ("custom_components.ventilation_coach", ROOT / "custom_components" / "ventilation_coach"),
):
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules[name] = module

failed = 0
for test_file in ("test_model", "test_engine"):
    spec_path = ROOT / "tests" / f"{test_file}.py"
    module = types.ModuleType(test_file)
    module.__file__ = str(spec_path)
    exec(compile(spec_path.read_text(), spec_path, "exec"), module.__dict__)
    for name, func in vars(module).items():
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS {test_file}::{name}")
            except Exception:  # noqa: BLE001
                failed += 1
                print(f"FAIL {test_file}::{name}")
                traceback.print_exc()

print("failed:", failed)
sys.exit(1 if failed else 0)
