"""Evaluate the one GitHub Actions expression the refiner's call caps use (issue #1345).

The sweep builds `CLAUDE_CODE_SCRIPT_CAPS` with `format()` from the
`close_cap` input, so the tests read the JSON the runner would see. Every
other caps value is plain JSON and comes back unchanged.
"""
import re

_FORMAT = re.compile(r"\$\{\{\s*format\('(.*)',\s*inputs\.close_cap\s*\)\s*\}\}", re.S)


def github_caps(value: str, close_cap: int) -> str:
    """The caps JSON for `value` at run time, with `inputs.close_cap` set to `close_cap`.

    GitHub's `format()` reads `{{` and `}}` as literal braces and `{0}` as
    the first argument.
    """
    value = value.strip()
    match = _FORMAT.fullmatch(value)
    if match is None:
        return value
    text = match.group(1).replace("{{", "\0").replace("}}", "\1")
    return text.replace("{0}", str(close_cap)).replace("\0", "{").replace("\1", "}")
