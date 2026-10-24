from bh02_lab.battery import outcome


def _report(tests: int, failures: int = 0, errors: int = 0, skipped: int = 0) -> str:
    return (
        f'<testsuites><testsuite tests="{tests}" failures="{failures}" errors="{errors}" '
        f'skipped="{skipped}"/></testsuites>'
    )


def test_all_expected_checks_passing_passes():
    assert outcome(_report(6), 6).passed


def test_a_failure_fails():
    result = outcome(_report(6, failures=1), 6)
    assert not result.passed and "5/6" in result.detail


def test_a_skip_fails():
    assert not outcome(_report(6, skipped=1), 6).passed


def test_fewer_checks_than_the_task_has_fail():
    result = outcome(_report(5), 6)
    assert not result.passed and "5 checks ran" in result.detail


def test_a_report_that_is_not_xml_fails():
    assert not outcome("<testsuite", 1).passed


def test_a_bare_testsuite_root_is_read():
    assert outcome('<testsuite tests="2" failures="0" errors="0" skipped="0"/>', 2).passed
