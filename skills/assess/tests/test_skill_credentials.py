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


# GitHub's REST API authentication docs and Slack's bot token docs publish these as examples.
GITHUB_DOCS_SAMPLE = "gh" + "p_16C7e42F292c6912E7710c838347Ae178B4a"
SLACK_DOCS_SAMPLE = "xo" + "xb-17653672481-19874698323-pdFZKVeTuE8sk7oOcBrzbqgy"
STRIPE_DOCS_SAMPLE = "sk" + "_test_4eC39HqLyjWDarjtT1zdp7dc"


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
    # A prefixed name with no provider prefix in the value: the commonest shape in skill docs.
    f"MY_SERVICE_API_KEY={_body(32)}",
    f"DB_PASSWORD='{_body(24)}'",
    f"AWS_SECRET_ACCESS_KEY={_body(40, B64)}",
    f"SECRET_KEY = '{_body(50, ALNUM + '!@#%^&*-_')}'",
    f"api_key: {_body(20, HEX)}",  # a short hex value, under the hex entropy threshold
    f'"env": {{"API_KEY": "{_body(32)}"}}',  # a quoted name: an MCP env block
    # A signed JWT: dotted, but its segments are half capitals and digits, so it is no attribute path (#1522).
    f"token = {_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64({'sub': _body(16)})}.{_body(43, URLSAFE)}",
    # A random value with a dot is no attribute path either (#1522).
    f"token = {_body(20, string.ascii_letters)}.{_body(2, string.ascii_letters)}",
    f"api_key = {_body(16)}.{_body(16)}",
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
    # Providers' published example tokens, exact matches only (see the test below for a near miss).
    "export GITHUB_TOKEN=" + GITHUB_DOCS_SAMPLE,
    "SLACK_BOT_TOKEN=" + SLACK_DOCS_SAMPLE,
    # Hand-typed bodies at the provider's length: interleaved ascending, and periodic.
    "gh" + "p_1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q8r",
    "sk-" + "abc123" * 8,
    "secret_key: " + STRIPE_DOCS_SAMPLE,
    'url = "https://x-access-token:' + 'gh' + 's_abc123@github.com/a/b",',  # a short value before the host
    "api_key: Optional[str] = None",
    # Hand-typed values in an assignment (#1522): interleaved ascending and periodic, in the
    # ``NAME=`` and ``name =`` forms as well as bare.
    "GITHUB_TOKEN=gh" + "p_1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q8r",
    "token = gh" + "p_1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q8r",
    "api_key: 1a2b3c4d5e6f7g8h9i0j",
    "export OPENAI_API_KEY=sk-" + "abc123" * 8,
    "token = sk-" + "abc123" * 8,
    'token = "' + "Kq7Wm2Xp9Rz4" * 3 + '"',
    "Kq7Wm2Xp9Rz4" * 3,
    # An attribute path in a code snippet is a reference, not a value (#1522).
    "apiKey: this.configService.anthropicKey,",
    "api_key = settings.openaiApiKey2",
    'const client = new Anthropic({ apiKey: this.config.anthropic.apiKey });',
    "token: self.settings.githubToken",
    "apiKey: this.config2Service.anthropicKey,",  # a digit inside a segment name
    # A repeated block behind a provider prefix, bare and assigned (#1522).
    "gh" + "p_" + "Kq7Wm2Xp9Rz4" * 3,
    "GITHUB_TOKEN=gh" + "p_" + "Kq7Wm2Xp9Rz4" * 3,
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


@pytest.mark.parametrize("sample", [GITHUB_DOCS_SAMPLE, SLACK_DOCS_SAMPLE, STRIPE_DOCS_SAMPLE],
                         ids=["github-docs", "slack-docs", "stripe-docs"])
def test_a_published_sample_is_set_aside_on_an_exact_match_only(sample: str) -> None:
    assert "credential" not in kinds_in(f"Use {sample} as in the provider's docs.")
    assert "credential" not in kinds_in(f"API_KEY={sample}")
    near = sample[:-1] + ("Z" if sample[-1] != "Z" else "Y")  # one character off: a real-shaped key
    assert "credential" in kinds_in(f"API_KEY={near}")


@pytest.mark.parametrize(("n", "alphabet"), [(20, HEX), (32, HEX), (40, HEX), (20, ALNUM), (36, ALNUM), (40, B64)])
def test_recall_holds_over_many_random_bodies(n: int, alphabet: str) -> None:
    rng = random.Random(n * len(alphabet))
    bodies = ["".join(rng.choice(alphabet) for _ in range(n)) for _ in range(2000)]
    found = sum("credential" in kinds_in(f"API_KEY={body}") for body in bodies)
    assert found / len(bodies) >= 0.97, found


@pytest.mark.parametrize(("left", "right", "alphabet"), [(20, 2, string.ascii_letters), (16, 16, ALNUM),
                                                      (24, 8, string.ascii_lowercase + string.digits)])
def test_recall_holds_over_many_random_dotted_values(left: int, right: int, alphabet: str) -> None:
    """The attribute-path rule (#1522) reads a camelCase name as a reference; a random dotted value
    stays a credential at the same gate as an undotted one."""
    rng = random.Random(left * right * len(alphabet))

    def body(n: int) -> str:
        return "".join(rng.choice(alphabet) for _ in range(n))
    found = sum("credential" in kinds_in(f"token = {body(left)}.{body(right)}") for _ in range(2000))
    assert found / 2000 >= 0.97, found


@pytest.mark.parametrize("shape", ["slack", "openai-project"])
def test_provider_key_recall_holds_over_many_random_bodies(shape: str) -> None:
    rng = random.Random(len(shape))

    def body(n: int, alphabet: str) -> str:
        return "".join(rng.choice(alphabet) for _ in range(n))
    keys = [("xo" + "xb-" + f"{body(12, string.digits)}-{body(13, string.digits)}-{body(24, ALNUM)}")
            if shape == "slack" else "sk-" + "proj-" + body(156, URLSAFE) for _ in range(2000)]
    found = sum("credential" in kinds_in(f"Use {key} here.") for key in keys)
    assert found / len(keys) >= 0.995, found


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
