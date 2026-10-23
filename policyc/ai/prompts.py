"""Prompt templates for the two AI stages, kept in one place so they can be
reviewed and versioned. PROMPT_VERSION fingerprints every template; the
evaluation records it so results from different prompts are never mixed.

The prompts only describe the language. Whether the answer is accepted is
decided by the compiler (and, for rewrites, the verifier), never by the model.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

GRAMMAR = """\
program       -> statement* EOF
statement     -> subject_decl | resource_decl | policy
subject_decl  -> SUBJECT IDENT ( ',' IDENT )*
resource_decl -> RESOURCE IDENT SUPPORTS action ( ',' action )*
policy        -> effect IDENT action IDENT ( WHEN or_expr )?
effect        -> ALLOW | DENY
action        -> READ | WRITE | EXECUTE | ACCESS
or_expr       -> and_expr ( OR and_expr )*
and_expr      -> not_expr ( AND not_expr )*
not_expr      -> NOT not_expr | primary
primary       -> IDENT | '(' or_expr ')'"""

SEMANTICS = """\
- Keywords are UPPERCASE. Names (subjects, resources, attributes) are identifiers:
  letters, digits and '_', starting with a letter or '_'. '#' starts a comment.
- Every subject and resource used in a rule must be declared first with SUBJECT / RESOURCE.
- RESOURCE x SUPPORTS lists the actions that make sense for x (READ, WRITE, EXECUTE).
  A rule may only use an action its resource supports. ACCESS is never listed in SUPPORTS.
- ACCESS in a rule means every action the resource supports.
- Each rule names exactly one subject, one action and one resource.
- Conditions use attribute names with AND, OR, NOT and parentheses. NOT binds
  tightest, then AND, then OR: `a OR b AND NOT c` means `a OR (b AND (NOT c))`.
- Decisions: if any matching DENY rule's condition is true the request is DENIED;
  otherwise if any matching ALLOW rule's condition is true it is ALLOWED;
  otherwise it is DENIED. So anything not explicitly allowed is already denied."""

EXAMPLE_POLICIES = (
    "SUBJECT admin, guest\n"
    "RESOURCE database SUPPORTS READ, WRITE\n"
    "ALLOW admin ACCESS database\n"
    "ALLOW guest READ database\n",

    "SUBJECT analyst, contractor\n"
    "RESOURCE reports SUPPORTS READ, WRITE\n"
    "RESOURCE build_api SUPPORTS EXECUTE\n"
    "ALLOW analyst READ reports WHEN authenticated\n"
    "ALLOW contractor EXECUTE build_api WHEN on_vpn AND NOT weekend\n"
    "DENY contractor ACCESS reports\n",
)

EXAMPLES = """\
Example 1
Requirement: Admins have full access to the database. Guests may only read it.
Policy:
%s
Example 2
Requirement: Analysts can read reports once authenticated. Contractors can run the
build API from the VPN, but never on weekends, and must never touch reports.
Policy:
%s""" % EXAMPLE_POLICIES

GEN_TEMPLATE = """\
You translate access-control requirements into the policyc DSL.

Grammar (EBNF):
{grammar}

Rules of the language:
{semantics}

{examples}

{vocabulary}

Requirement:
{english}

Output only the policy in the DSL: declarations first, then rules, one statement
per line. No explanations, no markdown."""

VOCAB_TEMPLATE = """\
Use exactly these declarations (copy them as the first lines of your policy) and
only these attribute names in conditions: {attributes}
{declarations}"""

NO_VOCAB = ("Choose short lowercase names for subjects, resources and attributes, "
            "and declare every subject and resource you use.")

FIX_TEMPLATE = """\
Your previous policy did not compile. Fix the errors the compiler reports and
change nothing else.

Grammar (EBNF):
{grammar}

Rules of the language:
{semantics}

{vocabulary}

Requirement:
{english}

Your previous policy (line numbers added, they are not part of the DSL):
{numbered}

Compiler errors (JSON):
{errors}

Output only the corrected policy in the DSL. No explanations, no markdown."""

OPT_TEMPLATE = """\
You optimize access-control policies written in the policyc DSL. Rewrite the
policy so it has fewer rules (or simpler conditions) but makes exactly the same
decision for every subject, action, resource and combination of attribute
values. A verifier will check your rewrite and reject any change in meaning.

Grammar (EBNF):
{grammar}

Rules of the language:
{semantics}

Allowed rewrites:
- drop duplicate rules;
- drop an ALLOW that an unconditional DENY for the same subject, action and resource always beats;
- drop a conditional rule when an unconditional rule with the same effect covers it;
- merge rules that differ only in action into one ACCESS rule, but only when together
  they cover every action the resource supports;
- merge rules with the same effect, subject, action and resource into one rule whose
  conditions are joined with OR;
- simplify conditions, e.g. `a AND b` plus `a AND NOT b` is just `a`.

Constraints:
- keep the SUBJECT and RESOURCE lines unchanged;
- keep every DENY rule (you may merge DENY rules with each other, never drop their effect).

Policy:
{source}

Its intermediate representation (one rule per subject, action, resource):
{ir}

Compiler warnings for this policy (JSON):
{warnings}
{feedback}
Output only the rewritten policy in the DSL. No explanations, no markdown."""

FEEDBACK_TEMPLATE = """
Previous attempt feedback:
{feedback}
"""


@dataclass
class Vocabulary:
    subjects: List[str]
    resources: Dict[str, List[str]]          # resource -> supported actions
    attributes: List[str] = field(default_factory=list)

    def declarations(self) -> str:
        lines = ["SUBJECT %s" % ", ".join(self.subjects)]
        lines.extend("RESOURCE %s SUPPORTS %s" % (name, ", ".join(actions))
                     for name, actions in self.resources.items())
        return "\n".join(lines)


def _vocabulary(vocabulary: Optional[Vocabulary]) -> str:
    if vocabulary is None:
        return NO_VOCAB
    return VOCAB_TEMPLATE.format(attributes=", ".join(vocabulary.attributes) or "(none)",
                                 declarations=vocabulary.declarations())


def _numbered(dsl: str) -> str:
    return "\n".join("%3d| %s" % (i, line) for i, line in enumerate(dsl.splitlines(), 1))


def generation_prompt(english: str, vocabulary: Optional[Vocabulary]) -> str:
    return GEN_TEMPLATE.format(grammar=GRAMMAR, semantics=SEMANTICS, examples=EXAMPLES,
                               vocabulary=_vocabulary(vocabulary), english=english.strip())


def generation_fix_prompt(english: str, vocabulary: Optional[Vocabulary], dsl: str,
                          errors: List[dict]) -> str:
    return FIX_TEMPLATE.format(grammar=GRAMMAR, semantics=SEMANTICS,
                               vocabulary=_vocabulary(vocabulary), english=english.strip(),
                               numbered=_numbered(dsl), errors=json.dumps(errors, indent=2))


def optimization_prompt(source: str, ir_dump: str, warnings: List[dict],
                        feedback: Optional[str]) -> str:
    return OPT_TEMPLATE.format(grammar=GRAMMAR, semantics=SEMANTICS, source=source.strip(),
                               ir=ir_dump, warnings=json.dumps(warnings, indent=2),
                               feedback=FEEDBACK_TEMPLATE.format(feedback=feedback)
                               if feedback else "")


def fingerprint() -> str:
    parts = (GRAMMAR, SEMANTICS, EXAMPLES, GEN_TEMPLATE, VOCAB_TEMPLATE, NO_VOCAB,
             FIX_TEMPLATE, OPT_TEMPLATE, FEEDBACK_TEMPLATE)
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:12]


PROMPT_VERSION = fingerprint()
