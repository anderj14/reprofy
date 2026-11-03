import runpy
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.parametrize("script", ["smoke_test.py", "classify_test.py", "repro_test.py", "fix_test.py", "repro_validation_demo.py", "evaluate_backends.py"])
def test_importing_scripts_does_not_load_credentials_or_create_api_clients(script):
    path = Path(__file__).resolve().parents[1] / "scripts" / script
    with (
        patch("openai.OpenAI", side_effect=AssertionError("Unexpected API client")),
        patch("dotenv.load_dotenv", side_effect=AssertionError("Unexpected dotenv load")),
    ):
        runpy.run_path(str(path), run_name="reprofy_import_probe")
