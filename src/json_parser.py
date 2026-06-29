# json_parser.py — robust extraction of the first JSON object from free text.
#
# Why this is needed: models in "thinking mode" often produce narrative text
# before and/or after the JSON, or emit multiple JSON objects in sequence.
# A plain json.loads(text) fails; text[text.index("{"):text.rindex("}")+1]
# fails when there is a second JSON after the first.
#
# Solution: scan the text counting { and }, ignoring those inside strings.
# Returns the FIRST balanced JSON object found.

import json


def extract_json(text):
    """
    Extract the first complete JSON object from a model response.
    Raises RuntimeError if no valid JSON is found.
    """
    try:
        start = text.index("{")
    except ValueError:
        raise RuntimeError(f"No JSON found in response:\n{text[:300]}")

    depth     = 0
    in_string = False
    escape    = False
    for i, c in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if c == "\\" and in_string:
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except json.JSONDecodeError as e:
                    raise RuntimeError(
                        f"Response is not valid JSON: {e}\n{text[:300]}"
                    )

    raise RuntimeError(f"No balanced JSON found:\n{text[:300]}")
