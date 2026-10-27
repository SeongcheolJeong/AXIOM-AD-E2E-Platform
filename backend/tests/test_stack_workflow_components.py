"""Documented workflows must not silently follow whichever catalog version is last."""
from copy import deepcopy
import importlib
from itertools import permutations
from pathlib import Path

import pytest

from ade2e.stack_runtime import StackRegistry


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "examples"))
    return importlib.import_module("stack_workflow_components")


def catalog():
    return [item.model_dump(mode="json") for item in StackRegistry(load_plugins=False).list()]


def test_all_builtin_catalog_orders_keep_v1_and_copy_its_defaults(helper):
    original = catalog()
    expected = helper.workflow_component_selections(original)
    v2 = deepcopy(original)
    for item in v2:
        item["version"] = "2"
        item["default_parameters"] = {"different_v2_schema": {"nested": [2]}}
    # Enumerate all orders of the actual six-release catalog (6!), not every
    # permutation of a duplicated twelve-entry catalog (12! = 479 million).
    for values in permutations(original):
        assert helper.workflow_component_selections(values) == expected
    extended = original + v2
    for offset in range(len(extended)):
        values = extended[offset:] + extended[:offset]
        assert helper.workflow_component_selections(values) == expected
        assert helper.workflow_component_selections(list(reversed(values))) == expected
    selected = helper.workflow_component_selections(original + v2)
    selected["perception"]["parameters"]["lookahead_m"] = 99.0
    assert helper.workflow_component_selections(original + v2) == expected


@pytest.mark.parametrize("alteration", ["missing", "unavailable", "wrong_role", "duplicate"])
def test_required_tuple_is_unique_available_and_has_exact_role(helper, alteration):
    values = catalog()
    target = next(item for item in values if item["id"] == "lidar-corridor")
    v2 = deepcopy(target)
    v2["version"] = "2"
    values.append(v2)
    if alteration == "missing":
        values.remove(target)
    elif alteration == "unavailable":
        target["status"] = "unavailable"
    elif alteration == "wrong_role":
        target["role"] = "controller"
    else:
        values.append(deepcopy(target))
    with pytest.raises(ValueError, match="lidar-corridor/1"):
        helper.workflow_component_selections(values)


@pytest.mark.parametrize("module_name", ["stack_replay_workflow", "carla_stack_workflow"])
def test_workflows_reject_missing_version_before_files_or_api_mutations(helper, module_name, tmp_path):
    module = importlib.import_module(module_name)
    class Api:
        def stack_components(self):
            values = catalog()
            for item in values:
                item["version"] = "2"
            return values
    output = tmp_path / "not-created"
    with pytest.raises(ValueError, match="lidar-corridor/1"):
        module.run(Api(), output)
    assert not output.exists()
