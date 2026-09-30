"""Remote workers still run Python 3.10; reject newer grammar before shipping."""
import ast
import io
import tokenize
from pathlib import Path


def test_backend_sources_parse_with_python_310_grammar():
    root = Path(__file__).resolve().parents[1]
    for file in root.rglob("*.py"):
        source = file.read_text(encoding="utf-8")
        ast.parse(source, filename=str(file), feature_version=(3, 10))
        # CPython's feature_version gate does not reject every PEP 701 change.
        # In particular, pre-3.12 rejects even escaped string literals inside
        # an f-string expression. Inspect tokenized expressions explicitly.
        frames = []
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == getattr(tokenize, "FSTRING_START", -1):
                frames.append(0)
            elif token.type == getattr(tokenize, "FSTRING_END", -1):
                frames.pop()
            elif frames and token.type == tokenize.OP and token.string == "{":
                frames[-1] += 1
            elif frames and token.type == tokenize.OP and token.string == "}":
                frames[-1] -= 1
            elif frames and frames[-1] and token.type == tokenize.STRING:
                assert "\\" not in token.string, f"{file}:{token.start[0]} uses a backslash in an f-string expression"
