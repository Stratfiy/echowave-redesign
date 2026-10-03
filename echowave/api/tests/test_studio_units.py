"""Studio, the parts that need no database: paths, the file batch, the
starter tree, the agents block, build logs, transcript compaction, the tool
catalogue, and the sandbox's build command."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

from api.services.studio import scaffold, sites
from api.services.studio import tools as studio_tools
from api.services.studio.session import compact

# --- paths -------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("src/App.jsx", "src/App.jsx"),
        ("./src/components/Hero.jsx", "src/components/Hero.jsx"),
        ("public/logo.svg", "public/logo.svg"),
        ("src\\pages\\About.jsx", "src/pages/About.jsx"),
    ],
)
def test_ordinary_paths_are_accepted(raw, expected):
    assert sites.clean_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "/etc/passwd",
        "../outside.js",
        "src/../../outside.js",
        "src//App.jsx",
        "node_modules/react/index.js",
        "dist/index.html",
        ".git/config",
        "src/App jsx",
        "src/$(rm -rf).js",
        "a" * 300,
    ],
)
def test_unsafe_or_generated_paths_are_refused(raw):
    with pytest.raises(sites.SiteError):
        sites.clean_path(raw)


# --- the file batch ----------------------------------------------------------


def _tree():
    return scaffold.starter_files(scaffold.FRAMEWORK_VITE_REACT, title="T")


def test_a_batch_writes_and_deletes_together():
    files, written, removed = sites.merged_files(
        _tree(),
        [{"path": "src/Hero.jsx", "content": "export default () => null;\n"}],
        ["src/index.css"],
    )
    assert written == ["src/Hero.jsx"]
    assert removed == ["src/index.css"]
    assert "src/Hero.jsx" in files and "src/index.css" not in files


def test_a_batch_with_one_bad_path_changes_nothing():
    original = _tree()
    with pytest.raises(sites.SiteError):
        sites.merged_files(
            original,
            [
                {"path": "src/Good.jsx", "content": "ok"},
                {"path": "../bad.js", "content": "no"},
            ],
        )
    assert "src/Good.jsx" not in original


def test_package_json_cannot_be_deleted():
    with pytest.raises(sites.SiteError, match="package.json"):
        sites.merged_files(_tree(), [], ["package.json"])


def test_content_must_be_text():
    with pytest.raises(sites.SiteError, match="text"):
        sites.merged_files(_tree(), [{"path": "src/a.js", "content": 42}])


def test_one_file_over_the_ceiling_is_refused():
    big = "x" * (sites.MAX_FILE_BYTES + 1)
    with pytest.raises(sites.SiteError, match="split"):
        sites.merged_files(_tree(), [{"path": "src/big.js", "content": big}])


def test_the_whole_tree_has_a_ceiling():
    chunk = "x" * (sites.MAX_FILE_BYTES - 10)
    count = sites.MAX_SOURCE_BYTES // len(chunk) + 1
    writes = [{"path": f"src/f{i}.js", "content": chunk} for i in range(count)]
    with pytest.raises(sites.SiteError, match="limit"):
        sites.merged_files(_tree(), writes)


def test_too_many_files_is_refused():
    writes = [{"path": f"src/f{i}.js", "content": "1"} for i in range(sites.MAX_FILES)]
    with pytest.raises(sites.SiteError, match="at most"):
        sites.merged_files(_tree(), writes)


# --- the starter tree --------------------------------------------------------


def test_the_starter_builds_with_relative_asset_urls():
    files = _tree()
    assert 'base: "./"' in files["vite.config.js"]
    package = json.loads(files["package.json"])
    assert package["scripts"]["build"] == "vite build"
    assert {"react", "react-dom"} <= set(package["dependencies"])
    assert scaffold.AGENTS_START in files["index.html"]
    assert scaffold.AGENTS_END in files["index.html"]


def test_a_title_cannot_break_the_page_or_the_script():
    title = 'Dr "Rao" </title><script>alert(1)</script>'
    files = scaffold.starter_files(scaffold.FRAMEWORK_VITE_REACT, title=title)
    assert "<script>alert(1)" not in files["index.html"]
    # The JSX gets the title as one JSON string literal, quotes escaped.
    assert json.dumps(title) in files["src/App.jsx"]


def test_an_unknown_framework_is_refused():
    with pytest.raises(ValueError):
        scaffold.starter_files("angular", title="x")


# --- the agents block --------------------------------------------------------


def test_the_agents_block_holds_exactly_the_snippets_given():
    html = _tree()["index.html"]
    once = scaffold.set_agents_block(html, ["<script>a</script>"])
    twice = scaffold.set_agents_block(once, ["<script>b</script>"])
    assert "<script>a</script>" not in twice
    assert twice.count("<script>b</script>") == 1
    assert twice.count(scaffold.AGENTS_START) == 1


def test_the_agents_block_comes_back_if_the_markers_were_removed():
    html = "<html><body><div id='root'></div></body></html>"
    out = scaffold.set_agents_block(html, ["<script>w</script>"])
    assert out.index("<script>w</script>") < out.lower().index("</body>")
    assert scaffold.AGENTS_START in out


# --- build logs --------------------------------------------------------------


def test_the_cause_of_a_failed_build_is_kept_and_the_stack_is_not():
    log = "\n".join(
        [
            "added 19 packages in 8s",
            "\x1b[36mvite v8.3.2 building for production...\x1b[39m",
            "✗ Build failed in 40ms",
            'error during build: src/App.jsx (3:10): Expected "}" but found ")"',
            "1: export default function App() {",
        ]
        + [
            f"    at internal{i} (file:///work/node_modules/x.mjs:1:1)"
            for i in range(80)
        ]
    )
    summary = sites.summarize_log(log)
    assert "src/App.jsx (3:10)" in summary
    assert "\x1b[" not in summary
    assert "internal79" not in summary


def test_a_log_with_no_error_lines_still_says_something():
    assert sites.summarize_log("one\ntwo\nthree") == "one\ntwo\nthree"


# --- transcript compaction ---------------------------------------------------


def test_compaction_removes_code_and_keeps_the_conversation():
    code = "export default function App() { return <h1>Hi</h1>; }"
    messages = [
        {"role": "user", "content": "make me a site"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "1",
                    "name": "write_site_files",
                    "arguments": {
                        "site_id": 7,
                        "files": [{"path": "src/App.jsx", "content": code}],
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "1",
            "name": "write_site_files",
            "content": {"written": ["src/App.jsx"]},
        },
        {
            "role": "tool",
            "tool_call_id": "2",
            "name": "read_site_file",
            "content": {"path": "src/App.jsx", "content": code},
        },
        {"role": "assistant", "content": "Done."},
    ]
    out = compact(messages)
    assert code not in json.dumps(out)
    call = out[1]["tool_calls"][0]["arguments"]
    assert call["files"][0]["path"] == "src/App.jsx"
    assert str(len(code)) in call["files"][0]["content"]
    assert out[2]["content"] == {"written": ["src/App.jsx"]}
    assert out[0] == messages[0] and out[-1] == messages[-1]
    # The input is untouched, and compacting twice changes nothing more.
    assert messages[1]["tool_calls"][0]["arguments"]["files"][0]["content"] == code
    assert compact(out) == out


# --- the tool catalogue ------------------------------------------------------


def test_studio_offers_every_agent_tool_it_names_and_every_site_tool():
    names = [schema["name"] for schema in studio_tools.tool_schemas()]
    assert len(names) == len(set(names))
    for name in studio_tools.AGENT_TOOLS:
        assert name in names
    for name in studio_tools.SITE_TOOL_NAMES:
        assert name in names


def test_studio_cannot_buy_numbers_or_connect_apps():
    names = {schema["name"] for schema in studio_tools.tool_schemas()}
    assert not names & {"buy_phone_number", "connect_app", "attach_app_tool"}


async def test_an_unknown_tool_is_an_error_the_model_can_read():
    out = await studio_tools.dispatch(
        "rm_rf", {}, session=None, organization_id=1, user_id=1
    )
    assert "Unknown tool" in out["error"]


# --- the sandbox's build entry ----------------------------------------------


def _sandbox(monkeypatch, network="decibyl_sandbox_build"):
    monkeypatch.setenv("SANDBOX_SECRET", "s")
    monkeypatch.setenv("SANDBOX_BUILD_NETWORK", network)
    path = Path(__file__).resolve().parents[2] / "sandbox" / "server.py"
    spec = importlib.util.spec_from_file_location("sandbox_server_under_test", path)
    module = importlib.util.module_from_spec(spec)
    # Registered first: the server's dataclasses look their module up by name.
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_a_build_box_is_hardened_and_on_the_build_network_only(monkeypatch):
    server = _sandbox(monkeypatch)
    command = server._build_command("abc", server.BuildRequest(files={}))
    joined = " ".join(command)
    assert command[command.index("--network") + 1] == "decibyl_sandbox_build"
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in command
    assert command[command.index("--user") + 1] == "1000:1000"
    assert "--privileged" not in joined and "docker.sock" not in joined


def test_builds_are_refused_without_a_build_network(monkeypatch):
    from fastapi.testclient import TestClient

    server = _sandbox(monkeypatch, network="")
    client = TestClient(server.app)
    response = client.post(
        "/builds",
        json={"files": {"package.json": "{}"}},
        headers={"x-sandbox-secret": "s"},
    )
    assert response.status_code == 503


def test_the_sandbox_refuses_a_path_outside_the_work_folder(monkeypatch):
    from fastapi.testclient import TestClient

    server = _sandbox(monkeypatch)
    client = TestClient(server.app)
    response = client.post(
        "/builds",
        json={"files": {"package.json": "{}", "../escape.js": "x"}},
        headers={"x-sandbox-secret": "s"},
    )
    assert response.status_code == 400
    response = client.post(
        "/builds", json={"files": {"package.json": "{}"}}, headers={}
    )
    assert response.status_code == 401


def test_build_output_with_a_hostile_name_is_dropped(monkeypatch):
    server = _sandbox(monkeypatch)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in (("./index.html", b"<html>"), ("../../etc/x", b"bad")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    out = server._unpack_output(base64.b64encode(buffer.getvalue()))
    assert list(out) == ["index.html"]
    assert base64.b64decode(out["index.html"]) == b"<html>"
