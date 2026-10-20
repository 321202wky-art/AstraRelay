"""Generic configuration has no bundled transports or automatic dotenv I/O."""

from pathlib import Path
import subprocess
import sys


def test_generic_environment_mapping_and_disabled_defaults():
    script = r'''
import os
import dotenv
dotenv.load_dotenv = lambda *a, **k: (_ for _ in ()).throw(AssertionError("dotenv I/O"))
for name in tuple(os.environ):
    if name.startswith(("GATEWAY_", "MODEL_GATEWAY_")) or name == "DEFAULT_MODEL_RUNTIME":
        os.environ.pop(name)
import config
assert config.GATEWAY_MAIN_BASE_URL == config.GATEWAY_MAIN_API_KEY == ""
assert config.GATEWAY_COMPAT_BASE_URL == config.GATEWAY_SECONDARY_BASE_URL == ""
assert len(config.MODEL_PROVIDER_CONNECTIONS) == 3
assert set(config.MODEL_PROVIDER_CONNECTIONS) == {"gateway_main", "gateway_compat", "gateway_secondary"}
assert all(value["url"] == "" for value in config.MODEL_CATALOG_PROVIDERS.values())
import importlib
os.environ["MODEL_GATEWAY_BASE_URL"] = "https://synthetic.example.invalid/v1"
os.environ["MODEL_GATEWAY_API_KEY"] = "<synthetic-gateway-credential>"
importlib.reload(config)
assert config.GATEWAY_MAIN_BASE_URL == "https://synthetic.example.invalid/v1"
assert config.GATEWAY_MAIN_API_KEY == "<synthetic-gateway-credential>"
assert config.DEFAULT_MODEL_RUNTIME == "gateway_main"
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
