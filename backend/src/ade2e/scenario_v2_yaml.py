"""Bounded, data-only YAML interchange; hashes derive from validated scenario data."""
import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode
from .scenario_v2_schemas import parse_scenario
from .scenario_v2_xml import digest


class ScenarioLoader(yaml.SafeLoader):
    def __init__(self, stream):
        self.node_count = 0
        self.depth = 0
        super().__init__(stream)

    def compose_node(self, parent, index):
        # Aliases are unnecessary for our canonical interchange and can amplify
        # memory or introduce cyclic objects. Reject all, including merge keys.
        if self.check_event(AliasEvent):
            raise ValueError('YAML aliases and cyclic references are not supported')
        self.node_count += 1
        self.depth += 1
        if self.node_count > 100000 or self.depth > 40:
            raise ValueError('YAML structure exceeds the supported size or depth')
        try:
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_mapping(self, node, deep=False):
        if not isinstance(node, MappingNode):
            raise ValueError('Expected a YAML mapping')
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise ValueError('YAML mapping keys must be strings')
            if key in result:
                raise ValueError(f'Duplicate YAML key: {key}')
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse_scenario_yaml(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > 2000000:
        raise ValueError('YAML must be a UTF-8 document of at most 2 MB')
    try:
        value = yaml.load(text, Loader=ScenarioLoader)
    except yaml.YAMLError as error:
        raise ValueError(f'Invalid data-only YAML: {error}') from error
    if not isinstance(value, dict):
        raise ValueError('YAML scenario must be a mapping')
    return parse_scenario(value)


def scenario_yaml(scenario):
    return yaml.safe_dump(scenario.model_dump(mode='json'), sort_keys=True, allow_unicode=True)


def yaml_result(scenario):
    value = scenario.model_dump(mode='json')
    return {'scenario': value, 'configuration_hash': digest(value), 'canonical_yaml': scenario_yaml(scenario)}
