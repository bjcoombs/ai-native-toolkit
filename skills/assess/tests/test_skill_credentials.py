"""The credential pattern for skill text (#1196): a labelled fixture set, a precision gate and no retention.

Every real-shaped key here is built at test time from a seeded random body, so no string in the
repository is itself shaped like a live credential (secret scanners read this file too).
"""
from __future__ import annotations

import base64
import json
import random
import string
from pathlib import Path

import pytest

from lib.counts_only.content_free import counts_only
from lib.skill_personal_data import FINDING_KINDS, kinds_in, scan_skill_personal_data

_RNG = random.Random(1196)
ALNUM = string.ascii_letters + string.digits
URLSAFE = ALNUM + "_-"
B64 = ALNUM + "+/"
HEX = "0123456789abcdef"


def _body(n: int, alphabet: str = ALNUM) -> str:
    return "".join(_RNG.choice(alphabet) for _ in range(n))


def _b64(obj: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode().rstrip("=")


# The jwt.io sample token, common in auth skill docs: an example, not a secret.
JWT_IO_SAMPLE = ".".join([_b64({"alg": "HS256", "typ": "JWT"}),
                          _b64({"sub": "1234567890", "name": "John Doe", "iat": 1516239022}),
                          "SflKxwRJSMeKKF2QT4" + "fwpMeJf36POk6yJV_adQssw5c"])


def _uuid() -> str:
    h = _body(32, HEX)
    return f"{h[:8]}-{h[8:12]}-4{h[13:16]}-a{h[17:20]}-{h[20:]}"


# Real-shaped: a known provider prefix with the provider's length, or a high-entropy value assigned to a
# credential name. Each must be a credential finding.
REAL: list[str] = [
    f"Use {'gh' + 'p_'}{_body(36)} for the API.",
    f"{'gh' + 'o_'}{_body(36)}",
    f"GITHUB_TOKEN={'gh' + 's_'}{_body(36)}",
    f"export GH_TOKEN={'gh' + 'u_'}{_body(36)}",
    f"{'github' + '_pat_'}{_body(22)}_{_body(59)}",
    f"ANTHROPIC_API_KEY={'sk-' + 'ant-api03-'}{_body(93, URLSAFE)}",
    f"key {'sk-' + 'ant-admin01-'}{_body(93, URLSAFE)} here",
    f"OPENAI_API_KEY={'sk-' + 'proj-'}{_body(120, URLSAFE)}",
    f"openai.api_key = '{'sk-'}{_body(48)}'",
    f"SLACK_BOT_TOKEN={'xo' + 'xb-'}{_body(12, string.digits)}-{_body(13, string.digits)}-{_body(24)}",
    f"{'xo' + 'xp-'}{_body(11, string.digits)}-{_body(12, string.digits)}-{_body(12, string.digits)}-{_body(32, HEX)}",
    f"stripe.api_key = \"{'sk' + '_live_'}{_body(24)}\"",
    f"{'rk' + '_live_'}{_body(99)}",
    f"key={'AI' + 'za'}{_body(35, URLSAFE)}",
    f"GITLAB_TOKEN={'gl' + 'pat-'}{_body(20, URLSAFE)}",
    f"//registry.npmjs.org/:_authToken={'np' + 'm_'}{_body(36)}",
    f"HF_TOKEN={'h' + 'f_'}{_body(34)}",
    f"SENDGRID_API_KEY={'S' + 'G.'}{_body(22, URLSAFE)}.{_body(43, URLSAFE)}",
    f"password = {'py' + 'pi-'}AgEIcHlwaS5vcmc{_body(70, URLSAFE)}",
    f'api_key = "{_body(32, HEX)}"',
    f"password: {_body(24, ALNUM + '!#%&*')}",
    f"secret: '{_body(40, B64)}'",
    f"  auth_token: {_body(32)}",
    f'access_key="{_body(20)}"',
    f"TOKEN={_uuid()}",
]

# Documentation placeholders and lookalikes: none may be a credential finding.
PLACEHOLDERS: list[str] = [
    "sk-...",
    'api_key = "sk-..."',
    "<API_KEY>",
    "api_key: <API_KEY>",
    "token = YOUR_TOKEN",
    "Set YOUR_TOKEN in the environment.",
    "export OPENAI_API_KEY=sk-your-key-here",
    "gh" + "p_" + "x" * 36,
    "gh" + "p_" + "X" * 36,
    "gh" + "p_<your-token>",
    "gh" + "p_1234567890abcdefghijklmnopqrstuvwxyz",
    "sk-ant-api03-...",
    "sk-ant-xxxxxxxx",
    "sk-proj-" + "X" * 48,
    "xo" + "xb-your-bot-token",
    "xo" + "xb-0000000000-0000000000000-" + "x" * 24,
    "AIzaSy...",
    "AI" + "za" + "X" * 35,
    "sk" + "_live_...",
    "sk" + "_live_" + "0" * 24,
    "token: ${GITHUB_TOKEN}",
    "api_key: ${{ secrets.OPENAI_API_KEY }}",
    'api_key = os.environ["OPENAI_API_KEY"]',
    "password: hunter2",
    "password: changeme",
    "token: abc123",
    "secret: my-super-secret-password",
    'api_key: "your-api-key"',
    "token: <token>",
    "api_key: EXAMPLE_API_KEY_1234567890",
    'token = "1234567890abcdef1234567890abcdef"',
    "api_key: sk-1234567890abcdefghijklmnopqrstuvwxyzABCDEFGHIJKL",
    "token: REDACTED",
    "password: ********",
    "api_key: [REDACTED]",
    "secret: config/secrets.yaml",
    "token: https://auth.example.com/oauth/token?v=2",
    "GITHUB_TOKEN=gh" + "p_YourPersonalAccessTokenGoesHere1234",
    "token: sk-ant-api03-EXAMPLE-KEY-ONLY",
    "Token: The value from step 2",
    "token: str",
    f'token = "{JWT_IO_SAMPLE}"',
    'url = "https://x-access-token:' + 'gh' + 's_abc123@github.com/a/b",',  # a short value before the host
    "api_key: Optional[str] = None",
]


def test_the_fixture_set_is_large_enough() -> None:
    assert len(REAL) + len(PLACEHOLDERS) >= 40 and len(REAL) >= 20


@pytest.mark.parametrize("text", REAL, ids=[f"real-{i}" for i in range(len(REAL))])  # ids hold no key
def test_a_real_shaped_key_is_a_credential(text: str) -> None:
    assert "credential" in kinds_in(text)


@pytest.mark.parametrize("text", PLACEHOLDERS, ids=[f"placeholder-{i}" for i in range(len(PLACEHOLDERS))])
def test_a_placeholder_is_not_a_credential(text: str) -> None:
    assert "credential" not in kinds_in(text)


def test_precision_and_recall_on_the_fixture_set_clear_the_gate() -> None:
    hits_real = sum("credential" in kinds_in(t) for t in REAL)
    hits_placeholder = sum("credential" in kinds_in(t) for t in PLACEHOLDERS)
    precision = hits_real / (hits_real + hits_placeholder)
    recall = hits_real / len(REAL)
    assert precision >= 0.95 and recall >= 0.8, (precision, recall)


def test_credential_is_a_finding_kind() -> None:
    assert "credential" in FINDING_KINDS


def test_no_matched_value_reaches_the_block_or_counts_only_output(tmp_path: Path) -> None:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text('{"name": "p"}\n', encoding="utf-8")
    for i, text in enumerate(REAL):
        skill = tmp_path / f"skills/s{i}/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(f"---\nname: s{i}\ndescription: Does s{i}.\n---\n{text}\n", encoding="utf-8")
    block = scan_skill_personal_data(tmp_path)
    assert block["files_with_personal_data"] == len(REAL)
    assert all(f["kinds"] == ["credential"] for f in block["files"])
    outputs = [json.dumps(block), json.dumps(counts_only({"skill_personal_data": block})),
               json.dumps(counts_only({"skill_personal_data": block}, hash_paths=True, salt="s"))]
    for text in REAL:
        # The longest run of key characters in the fixture: the secret, which no output may hold in part.
        secret = max(text.replace("=", " ").replace("'", " ").replace('"', " ").split(), key=len)
        for out in outputs:
            assert secret not in out and secret[-12:] not in out
