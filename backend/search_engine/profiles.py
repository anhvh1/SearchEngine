from .contracts import Profile
import json
import xml.etree.ElementTree as ET


def select(data, path):
    value = data
    for part in path.split('.') if path else []:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def matches(profile: Profile, data: dict) -> bool:
    return all(data.get(key) == value for key, value in profile.match.items())


def normalize(profile: Profile, data: dict) -> dict:
    attributes = {}
    for name, rule in profile.mapping.items():
        value = select(data, rule.path)
        if value is not None and rule.format != 'value':
            if not isinstance(value, str) or len(value.encode('utf8')) > 65536:
                raise ValueError('Structured vendor payload must be text no larger than 64 KiB')
            if rule.format == 'json':
                try:
                    value = select(json.loads(value), rule.selector)
                except (ValueError, RecursionError) as exc:
                    raise ValueError('Invalid vendor JSON') from exc
            else:
                # Only literal XML elements are accepted; no DTD, entities, XPath axes or predicates.
                if '<!' in value or '..' in rule.selector or rule.selector.startswith('/'):
                    raise ValueError('XML declarations/entities and relative traversal are not supported')
                try:
                    root = ET.fromstring(value)
                    element = root.find(rule.selector) if rule.selector else root
                    value = element.text if element is not None else None
                except ET.ParseError as exc:
                    raise ValueError('Invalid vendor XML') from exc
        if value is None:
            if rule.required:
                raise ValueError(f'Missing required field: {rule.path}')
            continue
        if rule.type == 'string':
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError(f'Expected scalar: {rule.path}')
            value = str(value)
        elif rule.type == 'integer':
            if isinstance(value, bool) or str(value).strip() != str(int(value)):
                raise ValueError(f'Expected integer: {rule.path}')
            value = int(value)
        elif rule.type == 'number':
            import math
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f'Expected finite number: {rule.path}')
        elif not isinstance(value, bool):
            raise ValueError(f'Expected boolean: {rule.path}')
        if rule.enum:
            if str(value) not in rule.enum:
                raise ValueError(f'Unknown enum: {rule.path}')
            value = rule.enum[str(value)]
        attributes[name] = value
    return attributes
