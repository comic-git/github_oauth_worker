"""Strict Jinja rendering for the worker's browser-facing HTML pages."""

from functools import cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape


def render_template(name: str, **context: object) -> str:
    """Render one worker-owned HTML template with explicit, autoescaped context values."""
    return _template_environment().get_template(name).render(**context)


@cache
def _template_environment() -> Environment:
    """Load packaged templates with autoescaping and strict missing-context failures."""
    return Environment(
        loader=FileSystemLoader(Path(__file__).with_name("templates")),
        autoescape=select_autoescape(("html", "tpl")),
        undefined=StrictUndefined,
    )
