from reprofy.repro_validation import assess_reproduction, static_reproduction_issue
from reprofy.test_runner import run_pytest
from reprofy.workspace import temporary_workspace
from tests.shop_fixture import BUGGY_SHOP


def _execute(code):
    with temporary_workspace(BUGGY_SHOP) as workspace:
        path = workspace / "tests/repro/test_probe.py"
        path.parent.mkdir(parents=True)
        path.write_text(code, encoding="utf-8")
        run = run_pytest(
            workspace, ["tests/repro/test_probe.py"],
            source_files=["shop/pricing.py"], timeout_seconds=5,
        )
    return run


def test_reproduction_requires_assertion_failure_after_project_execution():
    run = _execute(
        "from shop.pricing import checkout_total\n"
        "def test_coupon():\n"
        "    assert checkout_total(100, coupon_percent=150) >= 0\n"
    )
    accepted, reason = assess_reproduction(
        run, test_path="tests/repro/test_probe.py", source_files=["shop/pricing.py"],
        code="from shop.pricing import checkout_total\ndef test_coupon():\n    assert checkout_total(100, coupon_percent=150) >= 0\n",
    )
    assert accepted, reason
    assert run.evidence["reports"][1]["exception_type"] == "builtins.AssertionError"
    assert "shop/pricing.py" in run.evidence["reports"][1]["called_files"]


def test_import_error_and_bad_call_are_not_behavior_reproductions():
    import_failure = _execute(
        "import missing_reprofy_dependency\n"
        "def test_import():\n    assert True\n"
    )
    accepted, _ = assess_reproduction(
        import_failure, test_path="tests/repro/test_probe.py", source_files=["shop/pricing.py"],
        code="import missing_reprofy_dependency\ndef test_import():\n    assert True\n",
    )
    assert not accepted

    call_failure = _execute(
        "from shop.pricing import checkout_total\n"
        "def test_wrong_keyword():\n"
        "    assert checkout_total(100, coupon_percent=150, unknown=True) == 0\n"
    )
    accepted, reason = assess_reproduction(
        call_failure, test_path="tests/repro/test_probe.py", source_files=["shop/pricing.py"],
        code="from shop.pricing import checkout_total\ndef test_wrong_keyword():\n    assert checkout_total(100, coupon_percent=150, unknown=True) == 0\n",
    )
    assert not accepted
    assert "exception rather than a behavior assertion" in reason


def test_static_checks_reject_shortcuts_and_syntax_errors():
    assert "deliberately raises" in static_reproduction_issue("def test_x():\n    raise AssertionError('bug')\n")
    assert "constant assertion" in static_reproduction_issue("def test_x():\n    assert False\n")
    assert "invalid Python syntax" in static_reproduction_issue("def test_x(:\n")
