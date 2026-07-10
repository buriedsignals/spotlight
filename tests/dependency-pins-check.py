#!/usr/bin/env python3
"""Guard Spotlight setup against unreviewed package installs."""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "install-spotlight.sh").read_text()
SETUP = (ROOT / "setup.html").read_text()
CONFIGURE = (ROOT / "install" / "configure.html").read_text()
MANIFEST_PATH = ROOT / "VALIDATED_DEPENDENCIES.md"
MANIFEST = MANIFEST_PATH.read_text()

EXPECTED = {
    "firecrawl-cli": "1.9.8",
    "@tobilu/qmd": "2.5.3",
    "dev-browser": "0.2.8",
    "@anthropic-ai/claude-code": "2.1.169",
    "@google/gemini-cli": "0.45.2",
    "@openai/codex": "0.138.0",
    "opencode-ai": "1.17.7",
    "@earendil-works/pi-coding-agent": "0.79.6",
    "jsonschema": "4.25.1",
    "requests": "2.32.5",
    "maigret": "0.4.4",
    "crawl4ai": "0.9.0",
    "navigator-cli": "0.1.0",
}

DOCKER_ARTIFACTS = [
    ".devcontainer/devcontainer.json",
    "container/Dockerfile",
    "container/apt-packages.txt",
    "container/dpkg-manifest.txt",
    "container/package.json",
    "container/package-lock.json",
    "container/requirements.txt",
    "docs/sandboxing.md",
    "tests/sandbox-check.sh",
]

LOOSE_NPM_INSTALLS = [
    "firecrawl-cli",
    "@tobilu/qmd",
    "dev-browser",
    "@anthropic-ai/claude-code",
    "@google/gemini-cli",
    "@openai/codex",
    "opencode-ai",
    "@earendil-works/pi-coding-agent",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


for relpath in DOCKER_ARTIFACTS:
    if (ROOT / relpath).exists():
        fail(f"Docker artifact still present: {relpath}")

for package, version in EXPECTED.items():
    if f"`{package}`" not in MANIFEST or f"`{version}`" not in MANIFEST:
        fail(f"{MANIFEST_PATH.name} missing {package}@{version}")

# The installer is the single pin authority. setup.html is a static landing
# page and install/configure.html is the local configurator — neither
# documents (or could drift from) the reviewed versions.
PIN_PATTERN = re.compile(r"@\d+\.\d+\.\d+|==\d+\.\d+")
for page_name, page in [("setup.html", SETUP), ("install/configure.html", CONFIGURE)]:
    match = PIN_PATTERN.search(page)
    if match:
        fail(f"{page_name} carries a version pin string ({match.group(0)}); pins live in install-spotlight.sh only")

if "releases/latest" in INSTALLER or "Downloading Tolaria latest release" in INSTALLER:
    fail("installer still downloads a moving Tolaria latest release")
if "The installer downloads Tolaria" in SETUP or "downloads Tolaria on macOS" in SETUP:
    fail("setup.html still claims Tolaria is automatically downloaded")

for stale in [
    "agent-setup-btn",
    "buildAgentManifest",
    "buildAgentPrompt",
    "spotlight-agent-manifest.json",
    "spotlight-agent-prompt.md",
    "spotlight-agent-setup.zip",
    "# Spotlight Agent Setup",
]:
    if stale in SETUP:
        fail(f"setup.html still contains agent setup prompt path: {stale}")

for package in LOOSE_NPM_INSTALLS:
    # Catch direct "npm install -g package" or "npm install -g --prefix X package"
    # forms. The approved installer path builds package@version through
    # ensure_npm_global_exact().
    loose_pattern = re.compile(
        rf"npm install -g(?: --prefix [^ \n]+)? ['\"]?{re.escape(package)}['\"]?(?:\s|$)"
    )
    if loose_pattern.search(INSTALLER):
        fail(f"loose npm install found for {package}")

    for path in ROOT.rglob("*"):
        if (
            not path.is_file()
            or ".git" in path.parts
            or path.name == "dependency-pins-check.py"
            or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2"}
        ):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if loose_pattern.search(text):
            fail(f"loose npm install found for {package} in {path.relative_to(ROOT)}")

if re.search(r"pip(?:3|) install(?: --user)?(?: --quiet)? jsonschema requests", INSTALLER):
    fail("loose pip install found for jsonschema/requests")

LOOSE_PIP_PATTERNS = {
    "jsonschema": re.compile(r"pip(?:3|) install(?: [^&|\n;]+)?\bjsonschema\b(?!==)"),
    "requests": re.compile(r"pip(?:3|) install(?: [^&|\n;]+)?\brequests\b(?!==)"),
    "maigret": re.compile(r"pip(?:3|) install(?: [^&|\n;]+)?\bmaigret\b(?!==)"),
    "browser-use": re.compile(r"pip(?:3|) install(?: [^&|\n;]+)?\bbrowser-use\b(?!==)"),
}

for path in ROOT.rglob("*"):
    if (
        not path.is_file()
        or ".git" in path.parts
        or path.name == "dependency-pins-check.py"
        or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2"}
    ):
        continue
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        continue
    for package, pattern in LOOSE_PIP_PATTERNS.items():
        if pattern.search(text):
            fail(f"loose pip install found for {package} in {path.relative_to(ROOT)}")

for token in [
    "ensure_npm_global_exact firecrawl firecrawl-cli",
    "ensure_npm_global_exact qmd @tobilu/qmd",
    "ensure_npm_global_exact dev-browser dev-browser",
    "install_python_reviewed_deps",
    '"crawl4ai==$CRAWL4AI_VERSION"',
    '"navigator-cli==$NAVIGATOR_CLI_VERSION"',
]:
    if token not in INSTALLER:
        fail(f"installer missing reviewed dependency path: {token}")

print("dependency pins policy ok")
