"""A small JSON-schema checker (the subset our prompts use): type, required, properties, items, enum, minimum, maxLength."""

TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def errors(value, schema: dict, path: str = "$") -> list[str]:
    out = []
    types = schema.get("type")
    if types:
        types = types if isinstance(types, list) else [types]
        ok = any(isinstance(value, TYPES[t]) and not (t in ("integer", "number") and isinstance(value, bool)) for t in types)
        if not ok:
            return [f"{path}: ต้องเป็น {'/'.join(types)}"]
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: ค่าไม่อยู่ในรายการที่กำหนด")
    if isinstance(value, int | float) and not isinstance(value, bool) and "minimum" in schema and value < schema["minimum"]:
        out.append(f"{path}: ต่ำกว่า {schema['minimum']}")
    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        out.append(f"{path}: ยาวเกิน {schema['maxLength']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{path}.{key}: ขาด")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                out += errors(value[key], sub, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            out += errors(item, schema["items"], f"{path}[{i}]")
    return out
