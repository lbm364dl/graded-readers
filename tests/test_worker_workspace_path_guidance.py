import json
import shlex
import pytest
import pipeline.worker_workspace as workspace


@pytest.mark.parametrize('python3_path, expected_branch', [
    ('/tools/python3', 'detected'),
    ('/tools/python 3/bin/python3', 'detected with spaces'),
    (None, 'missing'),
])
def test_guidance_quotes_host_interpreter_and_matches_python3_detection(
        tmp_path, monkeypatch, python3_path, expected_branch):
    host_python = '/runtime/host python/bin/python'
    monkeypatch.setattr(workspace.sys, 'executable', host_python)
    monkeypatch.setattr(workspace.shutil, 'which',
                        lambda name: python3_path if name == 'python3' else None)
    payload = {'annotation': {'text': 'example'}, 'repository_reference': 'pipeline/example.py'}
    workspace.build(tmp_path, 'Task\nINPUT:\n' + json.dumps(payload), {'type': 'object'})

    readme = (tmp_path / 'README.txt').read_text()
    index = json.loads((tmp_path / 'INDEX.json').read_text())
    quoted = "'/runtime/host python/bin/python'"
    assert f'host runner Python is {host_python}' in readme
    assert f'its shell command is {quoted}' in readme
    assert str(workspace.ROOT / 'pipeline/example.py') in readme
    assert 'this workspace/inputs/01-00-annotation.json' in readme
    assert any(row['path'] == 'inputs/01-00-annotation.json' for row in index)
    assert (tmp_path / 'inputs/01-00-annotation.json').exists()
    assert (tmp_path / 'inputs/01-00-annotation.json').resolve() != (
        workspace.ROOT / 'inputs/01-00-annotation.json').resolve()
    assert 'Tools are enabled' in (tmp_path / 'TASK.txt').read_text()
    if expected_branch.startswith('detected'):
        assert 'python3 is available at ' + python3_path in readme
        assert 'python3 was not detected' not in readme
    else:
        assert 'python3 was not detected' in readme
        assert f'use the supplied host interpreter command {quoted}' in readme
        assert 'python3 is available at' not in readme
