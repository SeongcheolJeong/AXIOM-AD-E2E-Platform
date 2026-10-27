"""Bundle allowlist and dependency closure: no deployment or external downloads."""
import importlib.util
import json
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / 'build_local_pilot.py'
spec = importlib.util.spec_from_file_location('pilot_builder', path)
builder = importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


@pytest.fixture
def source(tmp_path, monkeypatch):
    root=tmp_path/'source';root.mkdir()
    for module in builder.MODULES:
        p=root/module;p.mkdir(parents=True);(p/'__init__.py').write_text('# authored fixture')
    (root/'frontend/dist').mkdir(parents=True)
    (root/'frontend/dist/index.html').write_text('<html>authored static fixture</html>')
    (root/'examples').mkdir()
    for name in ('dataset_evaluation_workflow.py','generate_perception_fixture.py','stack_replay_workflow.py','generate_stack_recording.py','stack_workflow_components.py','scene_discovery_workflow.py'):
        (root/'examples'/name).write_text('# workflow fixture')
    (root/'docs').mkdir()
    for name in ('product-pilot.md','local-pilot.md','workspace-backup.md','open-source-register.md','internal-platform.md','cases.md','case-conditions.md','case-retests.md','case-model-replays.md','case-stack-replays.md','collections.md','automatic-labels.md','scene-discovery.md','verification-plans.md','driving-policies.md'):
        (root/'docs'/name).write_text('Fixture limitations')
    (root/'.env').write_text('PRIVATE_TOKEN=do-not-package')
    (root/'.data').mkdir();(root/'.data/private.json').write_text('customer data')
    (root/'scripts').mkdir();(root/'scripts/native_stop_observer_gdb.py').write_text('research')
    monkeypatch.setattr(builder,'dependencies',lambda: {'sample-runtime':'1.0'})
    monkeypatch.setattr(builder.subprocess,'check_output',lambda args,**kwargs: 'fixture-base-commit\n' if kwargs.get('text') else b' M file')
    return root


def test_package_is_explicit_source_and_ui_not_data_or_research(source,tmp_path):
    out=tmp_path/'app';result=builder.build(source,out)
    manifest=json.loads((out/'bundle.json').read_text())
    assert result['status']=='built' and manifest['source_worktree_dirty'] is True
    assert not manifest['clean_checkout_release'] and not manifest['signed']
    assert 'run-pilot.py' in manifest['files'] and 'ui/index.html' in manifest['files']
    assert not (out/'.env').exists() and not (out/'.data').exists() and not (out/'scripts').exists()
    assert 'do-not-package' not in (out/'bundle.json').read_text()
    assert (out/'requirements-pilot.txt').read_text()=='sample-runtime==1.0\n'


def test_builder_does_not_overwrite_existing_bundle(source,tmp_path):
    out=tmp_path/'existing';out.mkdir();(out/'sentinel').write_text('preserve')
    with pytest.raises(ValueError):builder.build(source,out)
    assert (out/'sentinel').read_text()=='preserve'


def test_builder_requires_compiled_ui(source,tmp_path):
    (source/'frontend/dist/index.html').unlink()
    with pytest.raises(ValueError,match='Build frontend'):builder.build(source,tmp_path/'app')


def test_ui_symlinks_cannot_package_private_files(source,tmp_path):
    (source/'frontend/dist/private').symlink_to(source/'.env')
    with pytest.raises(ValueError,match='symlink'):builder.build(source,tmp_path/'app')
    assert not (tmp_path/'app').exists()
