"""Tests for the security verification layer (Z3 + enumeration + differential).

Run:  python -m unittest discover -s tests -v
"""
import io
import itertools
import json
import os
import random
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import z3  # noqa: E402,F401
except ImportError:
    raise unittest.SkipTest("z3-solver not installed (pip install -r requirements.txt)")

from policyc.verification import (MalformedPolicy, PolicyLoadError, Rule,  # noqa: E402
                                  decide, load_policy, load_policy_text, normalize,
                                  verify_candidate, verify_differential,
                                  verify_enumerate, verify_z3)
from policyc.verification import z3_backend  # noqa: E402
from policyc.verification.__main__ import main as cli_main  # noqa: E402

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def pol(name):
    return load_policy(str(EXAMPLES / name))


# ---------------------------------------------------------------------------
# loader (standalone .pol reader used by the tests and the CLI)
# ---------------------------------------------------------------------------

class LoaderTests(unittest.TestCase):
    def test_access_is_expanded_with_ids_and_lines(self):
        rules = pol("optimizable.pol")
        self.assertEqual(len(rules), 9)
        self.assertEqual([(r.id, r.action) for r in rules[:2]], [("R1", "READ"), ("R2", "WRITE")])
        self.assertEqual(rules[0].line, rules[1].line)

    def test_condition_precedence_not_and_or(self):
        rules = load_policy_text("SUBJECT s\nRESOURCE r SUPPORTS READ\nALLOW s READ r WHEN a OR b AND NOT c\n")
        self.assertEqual(rules[0].cond, ("or", (("attr", "a"), ("and", (("attr", "b"), ("not", ("attr", "c")))))))

    def test_undefined_names_are_errors(self):
        with self.assertRaises(PolicyLoadError):
            load_policy_text("SUBJECT s\nRESOURCE r SUPPORTS READ\nALLOW ghost READ r\n")
        with self.assertRaises(PolicyLoadError):
            load_policy_text("SUBJECT s\nRESOURCE r SUPPORTS READ\nALLOW s WRITE r\n")

    def test_decide_matches_documented_semantics(self):
        rules = load_policy_text(
            "SUBJECT admin, guest\nRESOURCE db SUPPORTS READ, WRITE\n"
            "ALLOW admin ACCESS db\nALLOW guest READ db WHEN vpn\nDENY guest READ db WHEN on_public_wifi\n")
        self.assertEqual(decide(rules, "guest", "READ", "db", {"vpn": True, "on_public_wifi": True}), "DENY")
        self.assertEqual(decide(rules, "guest", "READ", "db", {"vpn": True}), "ALLOW")
        self.assertEqual(decide(rules, "guest", "READ", "db", {}), "DENY")
        self.assertEqual(decide(rules, "admin", "WRITE", "db", {}), "ALLOW")


# ---------------------------------------------------------------------------
# the demo story: good candidate passes, bad candidates are caught
# ---------------------------------------------------------------------------

class ExampleVerdictTests(unittest.TestCase):
    def setUp(self):
        self.original = pol("optimizable.pol")

    def test_good_candidate_proven_equivalent_by_z3(self):
        res = verify_z3(self.original, pol("candidate_good.pol"), mode="equivalent")
        self.assertTrue(res.proven)
        self.assertTrue(res.equivalent)
        self.assertEqual(res.verdict, "PROVEN")

    def test_bad_candidate_rejected_with_exact_counterexample(self):
        res = verify_z3(self.original, pol("candidate_bad.pol"))
        self.assertFalse(res.proven)
        self.assertEqual(str(res.counterexample),
                         "analyst READ reports (authenticated=False): "
                         "original says DENY, candidate says ALLOW")
        self.assertEqual(res.counterexample.direction, "weaker")

    def test_dropping_a_deny_guard_is_caught(self):
        res = verify_z3(self.original, pol("candidate_drops_deny.pol"))
        self.assertFalse(res.proven)
        self.assertEqual(str(res.counterexample), "guest READ database: original says DENY, candidate says ALLOW")

    def test_stricter_candidate_passes_non_weakening_but_not_equivalent(self):
        stricter = pol("candidate_stricter.pol")
        self.assertTrue(verify_z3(self.original, stricter, mode="non-weakening").proven)
        res = verify_z3(self.original, stricter, mode="equivalent")
        self.assertFalse(res.proven)
        self.assertEqual(res.counterexample.direction, "stricter")
        self.assertEqual(str(res.counterexample), "admin READ reports: original says ALLOW, candidate says DENY")

    def test_find_all_lists_every_weakened_request(self):
        res = verify_z3(self.original, pol("candidate_drops_deny.pol"), find_all=True)
        self.assertEqual([(c.subject, c.action, c.resource) for c in res.counterexamples],
                         [("guest", "READ", "database")])
        both = verify_z3(self.original, pol("candidate_bad.pol"), mode="equivalent", find_all=True)
        self.assertGreaterEqual(len(both.counterexamples), 1)

    def test_original_is_always_safe_against_itself(self):
        self.assertTrue(verify_candidate(self.original, self.original, mode="equivalent").proven)

    def test_empty_policies(self):
        self.assertTrue(verify_z3([], [], mode="equivalent").proven)
        self.assertTrue(verify_z3(self.original, [], mode="equivalent").proven is False)  # drops all ALLOWs
        self.assertTrue(verify_z3(self.original, []).proven)  # but nothing is weaker


# ---------------------------------------------------------------------------
# fail closed: anything short of a full proof is NOT a pass
# ---------------------------------------------------------------------------

class FailClosedTests(unittest.TestCase):
    def setUp(self):
        self.original = pol("optimizable.pol")
        self.good = pol("candidate_good.pol")

    def test_z3_unknown_is_not_a_proof(self):
        with mock.patch.object(z3.Solver, "check", return_value=z3.unknown):
            res = verify_z3(self.original, self.good, mode="equivalent")
        self.assertFalse(res.proven)
        self.assertEqual(res.verdict, "UNPROVEN")
        self.assertIn("unknown", res.reason)

    def test_encoding_self_check_catches_a_wrong_model(self):
        # Pretend the python replay disagrees with Z3: must NOT report a counterexample or a proof.
        with mock.patch.object(z3_backend, "decide_rules", return_value="DENY"):
            res = verify_z3(self.original, pol("candidate_bad.pol"))
        self.assertFalse(res.proven)
        self.assertFalse(res.counterexamples)
        self.assertIn("internal error", res.reason)

    def test_malformed_candidate_is_rejected(self):
        bad = [Rule("R1", "ALLOW", "admin", "ACCESS", "database")]  # ACCESS must be expanded
        res = verify_candidate(self.original, bad)
        self.assertFalse(res.proven)
        self.assertEqual(res.layer, "wellformed")
        self.assertIn("ACCESS", res.reason)

    def test_garbage_input_is_rejected_not_crashed(self):
        res = verify_candidate(self.original, 42)
        self.assertFalse(res.proven)

    def test_enumeration_refuses_past_its_cap_and_z3_does_not(self):
        attrs = ["a%d" % i for i in range(20)]
        base = "SUBJECT u\nRESOURCE r SUPPORTS READ\n"
        original = load_policy_text(base + "ALLOW u READ r WHEN " + " AND ".join(attrs) + "\n")
        reordered = load_policy_text(base + "ALLOW u READ r WHEN " + " AND ".join(reversed(attrs)) + "\n")
        weaker = load_policy_text(base + "ALLOW u READ r WHEN a0\n")

        enum_res = verify_enumerate(original, reordered, mode="equivalent", max_attrs=16)
        self.assertFalse(enum_res.proven)               # refuses, does not pass
        self.assertIn("cap exceeded", enum_res.reason)
        self.assertTrue(verify_z3(original, reordered, mode="equivalent").proven)

        z3_res = verify_z3(original, weaker)
        self.assertFalse(z3_res.proven)
        self.assertEqual(z3_res.counterexample.original, "DENY")
        self.assertEqual(len(z3_res.counterexample.env), 20)
        # Z3's counterexample must really be one: replay it through decide().
        self.assertEqual(decide(weaker, "u", "READ", "r", z3_res.counterexample.env), "ALLOW")
        self.assertEqual(decide(original, "u", "READ", "r", z3_res.counterexample.env), "DENY")

    def test_invalid_mode_and_backend(self):
        with self.assertRaises(ValueError):
            verify_z3(self.original, self.good, mode="maybe")
        with self.assertRaises(ValueError):
            verify_candidate(self.original, self.good, backend="magic")


# ---------------------------------------------------------------------------
# cross-check: Z3 and enumeration agree with an independent brute force
# ---------------------------------------------------------------------------

ATTRS = ["a", "b", "c", "d"]


def rand_tree(rng, depth=2):
    if depth == 0 or rng.random() < 0.35:
        return ("attr", rng.choice(ATTRS))
    tag = rng.choice(["not", "and", "or"])
    if tag == "not":
        return ("not", rand_tree(rng, depth - 1))
    return (tag, tuple(rand_tree(rng, depth - 1) for _ in range(rng.randint(2, 3))))


def rand_rules(rng, n):
    return [Rule("R%d" % (i + 1), rng.choice(["ALLOW", "DENY"]), rng.choice(["s1", "s2"]),
                 rng.choice(["READ", "WRITE"]), "r1", rng.choice([None, rand_tree(rng)]))
            for i in range(n)]


def mutate(rng, rules):
    rules = list(rules)
    for _ in range(rng.randint(0, 2)):
        op = rng.choice(["drop", "uncond", "flip", "add", "shuffle"])
        if op == "drop" and rules:
            rules.pop(rng.randrange(len(rules)))
        elif op == "uncond" and rules:
            i = rng.randrange(len(rules))
            rules[i] = Rule(rules[i].id, rules[i].effect, rules[i].subject, rules[i].action, rules[i].resource, None)
        elif op == "flip" and rules:
            i = rng.randrange(len(rules))
            r = rules[i]
            rules[i] = Rule(r.id, "DENY" if r.effect == "ALLOW" else "ALLOW", r.subject, r.action, r.resource, r.cond)
        elif op == "add":
            rules += rand_rules(rng, 1)
        elif op == "shuffle":
            rng.shuffle(rules)
    return rules


def brute_force(orig, cand, mode):
    """Independent ground truth using only model.decide()."""
    for key in {r.key for r in orig + cand}:
        for bits in itertools.product([False, True], repeat=len(ATTRS)):
            env = dict(zip(ATTRS, bits))
            d_o, d_c = decide(orig, *key, env), decide(cand, *key, env)
            if mode == "equivalent" and d_o != d_c:
                return False
            if mode == "non-weakening" and d_c == "ALLOW" and d_o == "DENY":
                return False
    return True


class CrossCheckTests(unittest.TestCase):
    def test_z3_enumeration_and_brute_force_agree_on_random_policies(self):
        rng = random.Random(20261012)
        seen = {"proven": 0, "rejected": 0}
        for _ in range(400):
            orig = rand_rules(rng, rng.randint(0, 6))
            cand = mutate(rng, orig) if rng.random() < 0.8 else rand_rules(rng, rng.randint(0, 6))
            for mode in ("non-weakening", "equivalent"):
                truth = brute_force(orig, cand, mode)
                z = verify_z3(orig, cand, mode=mode)
                e = verify_enumerate(orig, cand, mode=mode)
                self.assertEqual(z.proven, truth, "z3 disagrees on %s\n%s\n%s" % (mode, orig, cand))
                self.assertEqual(e.proven, truth, "enumerate disagrees on %s" % mode)
                if not truth:
                    # the counterexample must be real, in the right direction
                    cex = z.counterexample
                    self.assertNotEqual(decide(orig, cex.subject, cex.action, cex.resource, cex.env),
                                        decide(cand, cex.subject, cex.action, cex.resource, cex.env))
                    if mode == "non-weakening":
                        self.assertEqual(cex.direction, "weaker")
                seen["proven" if truth else "rejected"] += 1
        # make sure the test is not vacuous
        self.assertGreater(seen["proven"], 60)
        self.assertGreater(seen["rejected"], 60)

    def test_differential_never_rejects_a_safe_candidate(self):
        rng = random.Random(7)
        for _ in range(200):
            orig = rand_rules(rng, rng.randint(0, 6))
            cand = mutate(rng, orig)
            if brute_force(orig, cand, "non-weakening"):
                self.assertFalse(verify_differential(orig, cand).counterexamples)


# ---------------------------------------------------------------------------
# layered pipeline
# ---------------------------------------------------------------------------

class LayeredTests(unittest.TestCase):
    def setUp(self):
        self.original = pol("optimizable.pol")

    def test_bad_candidate_is_stopped_by_the_cheap_layer(self):
        res = verify_candidate(self.original, pol("candidate_bad.pol"))
        self.assertEqual(res.layer, "differential")
        self.assertFalse(res.proven)

    def test_good_candidate_needs_the_z3_layer(self):
        res = verify_candidate(self.original, pol("candidate_good.pol"), mode="equivalent")
        self.assertTrue(res.proven)
        self.assertTrue(res.layer.startswith("z3"))

    def test_differential_alone_never_claims_a_proof(self):
        res = verify_differential(self.original, pol("candidate_good.pol"), mode="equivalent")
        self.assertFalse(res.proven)
        self.assertIn("cannot prove", res.reason)

    def test_feedback_text_contains_the_counterexample(self):
        res = verify_candidate(self.original, pol("candidate_bad.pol"))
        text = res.feedback()
        self.assertIn("NOT safe", text)
        self.assertIn("analyst READ reports (authenticated=False)", text)

    def test_to_dict_is_json_serialisable(self):
        res = verify_candidate(self.original, pol("candidate_bad.pol"))
        data = json.loads(json.dumps(res.to_dict()))
        self.assertEqual(data["verdict"], "REJECTED")
        self.assertEqual(data["counterexamples"][0]["direction"], "weaker")

    def test_all_backends_give_the_same_verdict_on_the_examples(self):
        for cand in ("candidate_good.pol", "candidate_bad.pol", "candidate_drops_deny.pol", "candidate_stricter.pol"):
            for mode in ("non-weakening", "equivalent"):
                verdicts = {b: verify_candidate(self.original, pol(cand), mode=mode, backend=b).proven
                            for b in ("layered", "z3", "enumerate")}
                self.assertEqual(len(set(verdicts.values())), 1, (cand, mode, verdicts))


# ---------------------------------------------------------------------------
# adapter: the verifier must read the compiler's IR without knowing its classes
# ---------------------------------------------------------------------------

class Attr:       # style 1: leaf with .name
    def __init__(self, name): self.name = name


class Ident(Attr):
    pass


class Not:
    def __init__(self, operand): self.operand = operand


class And:        # style 1: binary left/right
    def __init__(self, left, right): self.left, self.right = left, right


class Or:         # style 2: operand list
    def __init__(self, *operands): self.operands = list(operands)


class FakeRule:
    def __init__(self, id, effect, subject, action, resource, condition=None, line=1):
        self.id, self.effect, self.subject, self.action = id, effect, subject, action
        self.resource, self.condition, self.line = resource, condition, line


class FakeIR:
    def __init__(self, rules): self.rules = rules


class AdapterTests(unittest.TestCase):
    def test_reads_ast_style_objects(self):
        cond = And(Attr("authenticated"), Not(Ident("on_public_wifi")))
        rules = normalize(FakeIR([FakeRule("R1", "ALLOW", "analyst", "READ", "reports", cond)]))
        self.assertEqual(rules[0].cond, ("and", (("attr", "authenticated"), ("not", ("attr", "on_public_wifi")))))

    def test_reads_operand_list_style(self):
        rules = normalize([FakeRule("R1", "ALLOW", "s", "READ", "r", Or(Attr("a"), Attr("b"), Attr("c")))])
        self.assertEqual(rules[0].cond, ("or", (("attr", "a"), ("attr", "b"), ("attr", "c"))))

    def test_reads_codegen_json_conditions(self):
        cond = {"and": [{"attr": "authenticated"}, {"not": {"attr": "on_public_wifi"}}]}
        rules = normalize([FakeRule("R1", "ALLOW", "s", "READ", "r", cond)])
        self.assertEqual(rules[0].cond, ("and", (("attr", "authenticated"), ("not", ("attr", "on_public_wifi")))))

    def test_end_to_end_with_fake_ir_objects(self):
        original = FakeIR([FakeRule("R1", "ALLOW", "analyst", "READ", "reports", Attr("authenticated"))])
        bad = FakeIR([FakeRule("R1", "ALLOW", "analyst", "READ", "reports", None)])
        res = verify_candidate(original, bad)
        self.assertEqual(str(res.counterexample),
                         "analyst READ reports (authenticated=False): original says DENY, candidate says ALLOW")

    def test_unknown_node_gives_a_clear_error_and_custom_adapter_fixes_it(self):
        class Weird:
            pass
        rule = FakeRule("R1", "ALLOW", "s", "READ", "r", Weird())
        with self.assertRaises(MalformedPolicy) as ctx:
            normalize([rule])
        self.assertIn("cond_adapter", str(ctx.exception))
        rules = normalize([rule], cond_adapter=lambda n: ("attr", "x") if isinstance(n, Weird) else NotImplemented)
        self.assertEqual(rules[0].cond, ("attr", "x"))

    def test_with_ir_must_be_unexpanded_access_is_rejected(self):
        with self.assertRaises(MalformedPolicy):
            normalize([FakeRule("R1", "ALLOW", "s", "ACCESS", "r")])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli_main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_exit_codes_and_output(self):
        orig = str(EXAMPLES / "optimizable.pol")
        code, out, _ = run_cli(["verify", orig, str(EXAMPLES / "candidate_good.pol")])
        self.assertEqual(code, 0)
        self.assertIn("PROVEN", out)

        code, out, _ = run_cli(["verify", orig, str(EXAMPLES / "candidate_bad.pol")])
        self.assertEqual(code, 1)
        self.assertIn("REJECTED", out)
        self.assertIn("authenticated=False", out)

        code, out, _ = run_cli(["verify", orig, str(EXAMPLES / "candidate_stricter.pol")])
        self.assertEqual(code, 0)
        code, out, _ = run_cli(["verify", orig, str(EXAMPLES / "candidate_stricter.pol"), "--mode", "equivalent"])
        self.assertEqual(code, 1)

    def test_json_output_and_bad_input(self):
        orig = str(EXAMPLES / "optimizable.pol")
        code, out, _ = run_cli(["verify", orig, str(EXAMPLES / "candidate_bad.pol"), "--json", "--backend", "z3"])
        self.assertEqual(json.loads(out)["backend"], "z3")
        code, _, err = run_cli(["verify", orig, "does_not_exist.pol"])
        self.assertEqual(code, 2)
        code, _, err = run_cli(["verify", orig, str(EXAMPLES / "syntax_errors.pol")])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
