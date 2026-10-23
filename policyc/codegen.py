"""Phase 6: code generation. IR -> JSON target policy.

A simplified policy document in the spirit of XACML / Cedar: combining
algorithm, default decision, declarations, and every rule with its condition
tree and source line (so any decision can be traced back to the DSL).
"""

from __future__ import annotations

import json

from . import logic
from .ir import IRProgram

FORMAT = "policyc-target/1"


def generate(ir: IRProgram) -> dict:
    return {
        "format": FORMAT,
        "combining_algorithm": "deny-overrides",
        "default_decision": "DENY",
        "subjects": list(ir.subjects),
        "resources": {name: list(actions) for name, actions in ir.resources.items()},
        "rules": [
            {
                "id": r.id,
                "effect": r.effect,
                "subject": r.subject,
                "action": r.action,
                "resource": r.resource,
                "condition": logic.to_tree(r.condition),
                "source_line": r.line,
            }
            for r in ir.rules
        ],
    }


def to_json(ir: IRProgram) -> str:
    return json.dumps(generate(ir), indent=2)
