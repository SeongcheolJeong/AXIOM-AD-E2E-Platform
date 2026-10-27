"""Keep documented stack workflows on their original component implementations."""
from copy import deepcopy


WORKFLOW_COMPONENTS = (
    ("perception", "lidar-corridor", "1"),
    ("policy", "stopping-headway", "1"),
    ("controller", "proportional-control", "1"),
)


def workflow_component_selections(catalog):
    """Select exact available tuples; catalog order never upgrades an example."""
    selections = {}
    for role, identity, version in WORKFLOW_COMPONENTS:
        matches = [item for item in catalog
                   if (item["id"], item["version"]) == (identity, version)]
        if len(matches) != 1 or matches[0]["role"] != role or matches[0]["status"] != "available":
            raise ValueError(f"Workflow requires one available {role} component {identity}/{version}")
        selections[role] = {"id": identity, "version": version,
                            "parameters": deepcopy(matches[0]["default_parameters"])}
    return selections
