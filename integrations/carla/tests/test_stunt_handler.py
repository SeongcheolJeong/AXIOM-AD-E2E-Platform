"""Transport contract checks; these do not establish native simulator behavior."""
import pytest

from ade2e.database import Database, Settings
from ade2e.experiment_schemas import ExperimentCreate
from ade2e.service import canonical_bytes, digest
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow
from ade2e.stunt_lab_schemas import StuntConfig, model_definition
from axiom_carla.stunt import StuntHandler


@pytest.fixture
def configured(tmp_path, monkeypatch):
    for key, value in dict(SSH='user@worker', PYTHON='/venv/bin/python', SCRIPT='/run/stunt_native.py',
                           DIR='/runs', OWNER='/run/owner.json', SPOOL='/run/spool', PORT='2400').items():
        monkeypatch.setenv('AXIOM_CARLA_STUNT_' + key, value)
    db = Database(Settings(data_dir=tmp_path, seed_examples=False)); db.initialize()
    handler = StuntHandler(database=db)
    return db, handler


def saved(db, **changes):
    value = dict(schema_version='stunt-lab-design/1', project_id='default', id='test-stunt', revision=1,
                 name='Test', config=StuntConfig(**changes).model_dump(), seed=42,
                 model_definition=model_definition(), fidelity='assumed_vehicle_rigid_body',
                 physical_validation='not_established', created_at='2026-10-23T00:00:00Z')
    with db.session() as session:
        session.add(SimulationCatalogRevisionRow(project_id='default', kind='stunt-design', id=value['id'],
            revision=1, manifest=canonical_bytes(value), manifest_hash=digest(value)))
        session.commit()
    return ExperimentCreate(name='Test', kind='object_sim', adapter_id='carla-stunt',
        input={'design_ref': dict(id=value['id'], revision=1, sha256=digest(value))})


def test_readiness_does_not_promote_installed_to_verified(configured, monkeypatch):
    _, handler = configured
    monkeypatch.setattr(handler, 'native_probe', lambda: {'available': True, 'physics_verified': False})
    assert handler.metadata().status == 'available'
    assert handler.readiness()['physics_verified'] is False


def test_preview_requires_commissioning_and_freezes_design(configured, monkeypatch):
    db, handler = configured; spec = saved(db)
    probe = {'available': True, 'physics_verified': False, 'map': 'Carla/Maps/Town10HD_Opt'}
    monkeypatch.setattr(handler, 'native_probe', lambda: probe)
    with pytest.raises(ValueError, match='commissioning'): handler.prepare(spec)
    probe['physics_verified'] = True
    snapshot = handler.prepare(spec)
    assert snapshot['design']['sha256'] == spec.input['design_ref']['sha256']
    assert snapshot['physical_validation'] == 'not_established'
    assert snapshot['transport_sha256'] == handler.transport_hash()
    with pytest.raises(ValueError, match='seed'): handler.prepare(spec.model_copy(update={'seed': 5}))


def test_ramp_is_not_silently_run_as_flat_kickflip(configured):
    db, handler = configured
    with pytest.raises(ValueError, match='collision asset'): handler.prepare(saved(db, preset='ramp'))


def test_transport_rejects_shell_paths_and_changed_snapshot(configured, tmp_path):
    _, handler = configured
    with pytest.raises(ValueError, match='transport changed'):
        handler.execute({'transport_sha256': '0' * 64}, tmp_path, lambda: False)
    handler.script = '/tmp/$(echo unsafe).py'
    with pytest.raises(ValueError, match='metacharacters'): handler.command(['--probe'])


def test_missing_transport_has_no_native_readiness(monkeypatch):
    monkeypatch.delenv('AXIOM_CARLA_STUNT_SSH', raising=False)
    handler = StuntHandler()
    assert handler.metadata().status == 'not_configured'
    assert handler.readiness() == {'available': False, 'physics_verified': False, 'reason': 'not_configured'}
