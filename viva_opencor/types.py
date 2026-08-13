"""Custom bigraph-schema types for viva-opencor.

Currently uses built-in types (map[float], any, maybe, ...) only. This hook
exists for future custom type registrations — e.g. unit-bearing CellML
variables keyed by ``component/variable``.
"""


def register_opencor_types(core):
    """Register schema types used by OpenCOR process outputs.

    ``numeric_result`` (nested trajectory dict) and ``any`` are not in
    bigraph-schema's BASE_TYPES; registering them as tree aliases lets composite
    wiring resolve the Steps' nested-dict outputs. Idempotent.
    """
    for name in ('any', 'numeric_result'):
        try:
            core.register_type(name, {'_inherit': 'tree'})
        except Exception:  # already registered on this core
            pass
    return core
