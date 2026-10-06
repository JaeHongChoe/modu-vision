"""Count filtered detector boxes per invocation across its selected input ROIs."""


def object_requirements(params):
    if "object_requirements" not in params:
        return None
    rules = params["object_requirements"]
    if not isinstance(rules, list) or not 1 <= len(rules) <= 64:
        raise ValueError("object_requirements needs 1-64 explicit class rules")
    seen = set()
    for row in rules:
        if not isinstance(row, dict) or set(row) - {"class_name", "min_count", "max_count"}:
            raise ValueError("object_requirements contains unsupported fields")
        name = row.get("class_name")
        lower, upper = row.get("min_count"), row.get("max_count")
        if (not isinstance(name, str) or not name.strip() or name != name.strip()
                or len(name) > 200 or name in seen
                or type(lower) is not int or not 0 <= lower <= 1_000_000
                or (upper is not None and (type(upper) is not int or not lower <= upper <= 1_000_000))):
            raise ValueError("object_requirements class names and count bounds are invalid")
        seen.add(name)
    return rules


def validate_object_vocabulary(rules, names):
    if not names or any(row["class_name"] not in names for row in rules):
        raise ValueError("object_requirements must name recorded detector foreground classes")


def count_objects(rules, regions):
    rows = []
    for rule in rules:
        count = sum(region.get("label") == rule["class_name"] for region in regions)
        upper = rule.get("max_count")
        passed = count >= rule["min_count"] and (upper is None or count <= upper)
        rows.append({**rule, "max_count": upper, "count": count, "verdict": "OK" if passed else "NG"})
    return rows
