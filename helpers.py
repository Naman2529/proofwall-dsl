import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")


def example(name):
    return os.path.join(EXAMPLES, name)


def read_example(name):
    with open(example(name), encoding="utf-8") as f:
        return f.read()


def codes(diags):
    return [d.code for d in diags]
