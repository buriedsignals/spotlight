#!/usr/bin/env python3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "spotlight_safe.py"


def run(*args):
    return subprocess.run([sys.executable, str(HELPER), *args], text=True, capture_output=True)


def assert_ok(*args):
    result = run(*args)
    if result.returncode != 0:
        raise AssertionError(f"expected ok for {args}: {result.stderr}")
    return result.stdout.strip()


def assert_reject(*args):
    result = run(*args)
    if result.returncode == 0:
        raise AssertionError(f"expected rejection for {args}: {result.stdout}")


def main():
    assert_ok("validate-url", "https://example.org/path?q=one%20two")
    for hostile in [
        'https://example.org/"; rm -rf / ; echo "',
        "javascript:alert(1)",
        "https://example.org/$(touch bad)",
        "https://example.org/a\nb",
    ]:
        assert_reject("validate-url", hostile)

    assert_ok("validate-doi", "10.1234/ABC-123_foo.bar")
    for hostile in ['10.1234/abc";rm -rf /', "doi:10.1234/abc", "10.12/nope"]:
        assert_reject("validate-doi", hostile)

    assert_ok("validate-slug", "sample-investigation_2026.05")
    for hostile in ["../case", "-rf", "case;rm", "case name", "case\nname"]:
        assert_reject("validate-slug", hostile)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "safe").mkdir()
        assert_ok("resolve-path", "--base", str(base), "--path", "safe/file.txt")
        for hostile in ["../outside", "/etc/passwd", "-rf", "safe/$(bad)", "safe/a\nb"]:
            assert_reject("resolve-path", "--base", str(base), "--path", hostile)

    print("shell-safety checks passed")


if __name__ == "__main__":
    main()
