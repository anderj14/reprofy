"""Safe YAML mappings with explicit duplicate-key rejection."""

import yaml


class YamlDataError(ValueError):
    pass


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or key in mapping:
            raise YamlDataError("Mapping keys must be unique strings")
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def parse_yaml_mapping(text: str) -> dict:
    try:
        raw = yaml.load(text, Loader=_UniqueLoader)
    except yaml.YAMLError as error:
        raise YamlDataError("Invalid YAML") from error
    if not isinstance(raw, dict):
        raise YamlDataError("YAML must contain a mapping")
    return raw
