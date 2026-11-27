"""lib/skill_personal_data.py and the skill-personal-data condition (#1045): the file and kind, never the data."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.run.run_scope import Scope
import lib.scoring.level_tally as lt
import lib.manifest.check_manifest as cm
import lib.unit_readings as ur
from lib.skill_personal_data import FINDING_KINDS, KINDS, kinds_in, scan_skill_personal_data
from test_unit_readings import _read, _write

CID = "skill-personal-data.1"
# The maintainer's approved why on #1045, byte for byte.
WHY = ("Skill text is loaded into every session that triggers it and copied wherever it is published, "
       "so personal data in it leaks to every user.")
SKILL = "---\nname: {0}\ndescription: Does {0}.\n---\n{1}\n"
# Built here so no literal personal datum sits in the repository's own text.
EMAIL = "jane.smith" + "@" + "acme-corp.io"
PHONE = "+44 20 7946 " + "0958"
# A high-entropy value, so a credential assignment around it is a finding (#1196); split as above.
KEY = "Q7rx9Lm2" + "Vt8Kp4Wz6Nb3"
KEY36 = KEY + "Hs5Jd1Fg7Ry2Mc9P"


def _plugin(root: Path, files: dict[str, str]) -> Path:
    for rel, text in {".claude-plugin/plugin.json": '{"name": "p"}\n', **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _state(block: Any) -> tuple[str, str | None]:
    cond = next(c for c in lt.level_result(lt.default_model(), {"skill_personal_data": block})["conditions"]
                if c["id"] == CID)
    return cond["state"], cond.get("reason")


# --- the kinds -------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "kinds"), [
    (f"Ask {EMAIL} for access.", ["email"]),
    (f"Call {PHONE} out of hours.", ["phone"]),
    ("Call (415) 555-" + "2671.", ["phone"]),
    ("Logs live in /Users/" + "jsmith/logs.", ["home_path"]),
    ("C:\\Users\\" + "John Smith\\Documents", ["home_path"]),  # a space, as the grader allows
    ("C:\\Users\\" + "Jos\u00e9\\x", ["home_path"]),             # an accented letter
    ("Reach the box at 10.20." + "30.40 first.", ["ip_address"]),
    ("The box is at 10.20." + "30.40.", ["ip_address"]),  # an address ending a sentence
    ("DB_HOST=10.20." + "30.40", ["ip_address"]),
    ("Pass --host=10.20." + "30.40 here.", ["ip_address"]),
    ("postgres://app@10.20." + "30.40:5432/db", ["ip_address"]),
    ("Run ssh deploy@" + "build-01 to deploy.", ["ssh_or_host"]),
    (f'password: "Str{KEY}"', ["credential"]),  # a literal that starts with a schema word
    (f"token: object{KEY}", ["credential"]),
    (f"token: list{KEY}", ["credential"]),
    (f"password: no.{KEY}", ["credential"]),
    (f"password: yes!{KEY}", ["credential"]),
    (f"password: str/{KEY}", ["credential"]),
    (f'password: str = "{KEY}"', ["credential"]),  # a typed field with a literal default
    (f"secret: bool={KEY}", ["credential"]),
    (f'password: "{KEY[:10]} {KEY[10:]}"', ["credential"]),  # quoted: still a value
    (f"password: {KEY}  # rotate me", ["credential"]),  # a comment after the value
    (f"PASSWORD={KEY} mysql -u app", ["credential"]),  # an assignment before a command
    (f"password: {KEY} then run", ["credential"]),  # secret-shaped, then prose
    (f"token: {'gh' + 'p_'}{KEY36} and more", ["credential"]),
    (f"Use {'gh' + 'p_'}{KEY36} once.", ["credential"]),  # a provider key needs no assignment
    (f"token: ${{{{ secrets.X }}}} and password = {KEY}", ["credential"]),  # one literal is enough
    ("AKIA" + "ABCDEFGHIJKLMNOP is the key.", ["cloud_key"]),
    ("-----BEGIN RSA PRIVATE KEY-----", ["private_key"]),
    (f"{EMAIL} or {PHONE}", ["email", "phone"]),
])
def test_each_kind_is_detected(text: str, kinds: list[str]) -> None:
    assert kinds_in(text) == kinds


@pytest.mark.parametrize("text", [
    "Write to user@example.com or someone@example.org.",
    "Co-authored-by: Claude <" + "noreply" + "@anthropic.com>",
    "12345+octocat@users." + "noreply.github.com",
    "a login@users." + "noreply` form, cut by formatting",
    "Clone git@" + "github.com:org/repo.git",
    "Files under /Users/.../ and /home/user/ and C:\\Users\\Default\\x",
    "Version 1.2.3.4 and port 8080, issue #1045, build 20261123",
    "Serve on 127.0.0.1 and 0.0.0.0.",
    "Requires v1.2.3.4 on 127.0.0.1.",
    "password = <your-password> and token=${TOKEN}",
    "token: ${{ secrets.GITHUB_TOKEN }}",
    "api_key: process.env.API_KEY",
    'token = os.environ["T"]',
    "password: changeme\ntoken: ${{ secrets.X }}",  # a placeholder beside a reference
    'password: ""\ntoken: ${{ secrets.X }}',
    "Released 2024-01-01 +0000 2024-01-02",
    "Use logo@2x.png and icon@3x.webp for retina screens.",
    "It saves +1000000000 tokens.",
    "Run ssh git@github.com to test auth.",
    "Run ssh deploy@example.com to try it.",
    "Log in as root@example.com first.",
    "def login(token: str) -> None: ...",
    "api_key: Optional[str] = None",
    "secret: true\npassword: required\ntoken: string",
    "Netmask 255.255.255.0 on 10.0.0.0/8",
    "Email: support@" + "anthropic.com for help.",  # a role address
    "pip install foo==1.2.3.4",
    "pin foo>=1.2.3.4 or foo~=1.2.3.4",
    "Example servers 192.0.2.10, 198.51.100.7 and 203.0.113.99",  # RFC 5737
    "Weights: +0.5 - 0.25 - 0.125",
    "Diff: +1234 -5678 lines",
    "Token: The value from step 2",
    "Password: Use the one in 1Password",
    "token: see below",
    # Example values, not generated secrets (#1196): short, low in entropy or a provider-key stub.
    "password: hunter" + "2",
    "password: nosfer" + "atu77",
    'password: "correct horse ' + 'battery"',
    "token = " + "ghp" + "_abc123def456",
    "token = _strip_anchor(m.group(1))",  # code, not a literal
])
def test_placeholders_and_lookalikes_are_not_findings(text: str) -> None:
    assert kinds_in(text) == []


def test_personal_names_are_not_detected_and_the_kinds_say_so() -> None:
    assert kinds_in("Written by Jane Smith and reviewed by Arjun Patel.") == []
    assert "name" not in " ".join(KINDS)
    assert KINDS == ("private_key", "cloud_key", "credential", "ssh_or_host", "ip_address", "home_path",
                     "email", "phone")


# --- the block -------------------------------------------------------------------------


def test_a_finding_names_the_file_and_kind_never_the_data(tmp_path: Path) -> None:
    repo = _plugin(tmp_path, {
        "skills/a/SKILL.md": SKILL.format("a", "Clean text."),
        "skills/a/references/contacts.md": f"Escalate to {EMAIL}.\n",
        "skills/b/SKILL.md": SKILL.format("b", f"Call {PHONE}."),
    })
    block = scan_skill_personal_data(repo)
    assert block["files"] == [{"path": "skills/a/references/contacts.md", "kinds": ["email"]},
                              {"path": "skills/b/SKILL.md", "kinds": ["phone"]}]
    assert (block["files_considered"], block["files_with_personal_data"]) == (3, 2)
    assert block["kinds_detected"] == list(FINDING_KINDS) and "credential" in block["kinds_detected"]
    dumped = json.dumps(block)
    assert EMAIL not in dumped and "7946" not in dumped and "jane" not in dumped
    assert _state(block) == ("unmet", None)


def test_a_references_symlink_out_of_the_repository_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.md").write_text(f"Escalate to {EMAIL}.\n", encoding="utf-8")
    repo = _plugin(tmp_path / "repo", {"skills/a/SKILL.md": SKILL.format("a", "Clean text.")})
    (repo / "skills/a/references").symlink_to(outside, target_is_directory=True)
    block = scan_skill_personal_data(repo)
    assert block["files"] == [] and _state(block) == ("met", None)


def test_a_large_reference_is_read_up_to_the_limit(tmp_path: Path) -> None:
    big = "x\n" * 600_000 + f"Escalate to {EMAIL}.\n"  # past the limit: not read
    repo = _plugin(tmp_path, {"skills/a/SKILL.md": SKILL.format("a", "Clean text."),
                              "skills/a/references/big.md": f"Escalate to {EMAIL}.\n" + big})
    block = scan_skill_personal_data(repo)
    assert [f["path"] for f in block["files"]] == ["skills/a/references/big.md"] and block["files_unread"] == 0


def test_an_unreadable_file_leaves_a_clean_reading_not_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _plugin(tmp_path, {"skills/a/SKILL.md": SKILL.format("a", "Clean text."),
                              "skills/a/references/locked.md": "text\n"})
    real_open = Path.open

    def fake_open(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if self.name == "locked.md":
            raise PermissionError(self)
        return real_open(self, *args, **kwargs)  # type: ignore[call-overload]
    monkeypatch.setattr(Path, "open", fake_open)
    block = scan_skill_personal_data(repo)
    assert block["files_unread"] == 1 and block["available"] is False
    assert block["reason"].startswith("skill_files_unread:")
    assert _state(block)[0] == "not_measured"


def test_a_reference_outside_the_scope_or_excluded_is_not_read(tmp_path: Path) -> None:
    repo = _plugin(tmp_path, {"skills/a/SKILL.md": SKILL.format("a", "Clean text."),
                              "skills/b/SKILL.md": SKILL.format("b", "Clean text."),
                              "skills/b/references/contacts.md": f"Escalate to {EMAIL}.\n",
                              "skills/c/SKILL.md": SKILL.format("c", "Clean text."),
                              "skills/c/references/contacts.md": f"Escalate to {EMAIL}.\n"})
    (repo / "skills/a/references").symlink_to(repo / "skills/b/references", target_is_directory=True)
    assert scan_skill_personal_data(repo, scope=Scope.of(repo, Path("skills/a")))["files"] == []
    excluded = scan_skill_personal_data(repo, excludes=(set(), ["contacts.md"]))  # a basename glob
    assert excluded["files"] == []


def test_a_credential_is_a_finding_and_an_example_value_is_information_only(tmp_path: Path) -> None:
    repo = _plugin(tmp_path, {"skills/a/SKILL.md": SKILL.format("a", f"Set password = {KEY}."),
                              "skills/b/SKILL.md": SKILL.format("b", "Set password = " + "hunter" + "2abc.")})
    block = scan_skill_personal_data(repo)
    assert block["files"] == [{"path": "skills/a/SKILL.md", "kinds": ["credential"]}]
    assert block["credential_candidates"] == 1  # the example value in b, set aside
    assert KEY not in json.dumps(block)
    assert _state(block) == ("unmet", None)


@pytest.mark.parametrize("value", ["xxxxxxxx", "******", "<your-token>", "${API_KEY}", "your_token_here",
                                   "your-key", "example123", "dummy-pass", "placeholder_value", "sk-abc123"])
def test_a_placeholder_credential_value_is_not_a_candidate(value: str) -> None:
    assert kinds_in(f"token = {value}") == []


def test_clean_skill_text_reads_met_and_no_skill_reads_not_measured(tmp_path: Path) -> None:
    repo = _plugin(tmp_path, {"skills/a/SKILL.md": SKILL.format("a", "Write to user@example.com.")})
    assert _state(scan_skill_personal_data(repo)) == ("met", None)
    empty = scan_skill_personal_data(_plugin(tmp_path / "none", {"README.md": f"{EMAIL}\n"}))
    assert empty["files_considered"] == 0 and _state(empty) == ("not_measured", "no_skill_files")


def test_the_condition_carries_the_approved_why_and_names_the_kinds() -> None:
    manifest = cm.core_manifest()
    cond = next(e for e in manifest["entries"]
                if e.get("key") == f"{CID}:skill_personal_data.files_with_personal_data")
    assert cond["why"] == WHY  # type: ignore[typeddict-item]
    for kind in ("email", "phone", "home path", "IP", "SSH login", "key", "credential"):
        assert kind in cond["title"]  # type: ignore[typeddict-item]
    assert not [c for c in lt.gate_conditions(lt.default_model()) if c.startswith("skill-personal-data.")]


# --- per skill unit --------------------------------------------------------------------


def test_each_skill_unit_reads_its_own_text(tmp_path: Path) -> None:
    repo = _write(tmp_path / "plugin", {
        ".claude-plugin/plugin.json": '{"name": "p"}\n', "agents/a.md": "---\nname: a\ndescription: A.\n---\n",
        "skills/clean/SKILL.md": SKILL.format("clean", "Nothing personal."),
        "skills/leaky/SKILL.md": SKILL.format("leaky", f"Mail {EMAIL}."),
    })
    units = {u["path"]: u for u in _read(repo)["units"]}
    clean = units["skills/clean"]["blocks"]["skill_personal_data"]
    leaky = units["skills/leaky"]["blocks"]["skill_personal_data"]
    assert (clean["files_considered"], clean["files_with_personal_data"]) == (1, 0)
    assert (leaky["files_considered"], leaky["files_with_personal_data"]) == (1, 1)
    assert "skill_personal_data" not in units["agents"]["blocks"]
    assert ur.unit_kinds(cm.core_manifest())["skill_personal_data"] == frozenset({"skill"})


def test_this_repository_holds_no_detected_personal_data_in_its_skills() -> None:
    # An invariant, not a pinned count: a skill that adds a real address should fail here.
    block = scan_skill_personal_data(Path(__file__).resolve().parents[3])
    assert block["files_considered"] > 0 and block["files"] == []
