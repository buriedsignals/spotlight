"""Render a tool list through the Gemma 4 chat template the way Osaurus does.

Osaurus's renderer raises when `upper` gets a value that is not a string (HTTP
500 "upper filter requires string"); Python's jinja2 would stringify it. The
`upper` here raises the same way; a missing value stays empty, as on Osaurus,
where tools whose parameters have no `type` render.
Reads {"tools": [...]} on stdin; exits 0 with the rendered prompt, 1 with the error.
"""
import json
import sys
from pathlib import Path

import jinja2


def strict_upper(value):
    if isinstance(value, jinja2.Undefined):
        return ""
    if not isinstance(value, str):
        raise ValueError(f"upper filter requires string, got {type(value).__name__}: {value!r}")
    return value.upper()


def raise_exception(message):
    raise ValueError(message)


class OsaurusEnvironment(jinja2.Environment):
    """An object schema with no `properties` makes the template walk the
    schema's own keys as if they were parameters (format_parameters with
    filter_keys). A numeric key such as minProperties then reaches
    `value['type'] | upper` as a number, which Osaurus refuses; jinja2 would
    quietly give Undefined."""

    def getitem(self, obj, argument):
        if isinstance(obj, (int, float)) and not isinstance(obj, bool):
            raise ValueError(f"upper filter requires string: {argument!r} of the number {obj!r}")
        return super().getitem(obj, argument)


env = OsaurusEnvironment(undefined=jinja2.ChainableUndefined, trim_blocks=False, lstrip_blocks=False)
env.filters["upper"] = strict_upper
env.globals["raise_exception"] = raise_exception
template = env.from_string((Path(__file__).parent / "chat_template.jinja").read_text())
tools = json.load(sys.stdin)["tools"]
try:
    print(template.render(messages=[{"role": "user", "content": "x"}], tools=tools, add_generation_prompt=True, bos_token="<bos>"))
except Exception as error:  # report which renderer error, for the test's message
    print(f"{type(error).__name__}: {error}", file=sys.stderr)
    sys.exit(1)
