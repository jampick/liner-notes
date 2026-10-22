"""A schema as a list of fields a person or a chat can read, for the paste prompt. The schema's
own $id, $schema and notes for developers stay out, and so do fields marked `x-reply-skip`;
`x-reply` notes, written for the reply, go in."""

TYPE = {"string": "text", "integer": "a whole number", "number": "a number", "boolean": "true or false", "null": "null"}


def _kind(s):
    if "enum" in s:
        return "one of " + ", ".join("null" if v is None else f'"{v}"' for v in s["enum"])
    t = s.get("type")
    ts = t if isinstance(t, list) else [t]
    names = []
    for x in ts:
        if x == "object":
            names.append("an object")
        elif x == "array":
            names.append("a list")
        elif x:
            names.append(TYPE.get(x, x))
    return " or ".join(names) or "anything"


def describe(schema, indent=0):
    """Bulleted lines, one per field, nested under objects and lists."""
    out = []
    pad = "  " * indent
    req = set(schema.get("required") or [])
    for name, s in (schema.get("properties") or {}).items():
        if s.get("x-reply-skip"):  # a person's field, or one only for checking
            continue
        bits = [_kind(s), "required" if name in req else "optional"]
        if s.get("minLength"):
            bits.append("not empty")
        if s.get("minItems"):
            bits.append(f"at least {s['minItems']}")
        note = f": {s['x-reply']}" if s.get("x-reply") else ""
        out.append(f"{pad}- `{name}` ({', '.join(bits)}){note}")
        sub = s.get("items") if s.get("items", {}).get("properties") else s if s.get("properties") else None
        if sub is not None:
            if s.get("items"):
                out.append(f"{pad}  each item has:")
            out += describe(sub, indent + 1)
    return out


def field_list(schema):
    return "\n".join(describe(schema))
