from pathlib import Path

path = Path("tests/test_lifecycle_concurrency_hardening.py")
text = path.read_text()
replacements = [
    (
        '''    monkeypatch.setattr(\n        "custom_components.conditional_notifications.manager.async_render", render\n    )\n''',
        '''    monkeypatch.setattr("custom_components.conditional_notifications.manager.async_render", render)\n''',
        2,
    ),
    (
        '''    monkeypatch.setattr(\n        "custom_components.conditional_notifications.manager.async_clear", clear\n    )\n''',
        '''    monkeypatch.setattr("custom_components.conditional_notifications.manager.async_clear", clear)\n''',
        1,
    ),
]
for old, new, expected in replacements:
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"Expected {expected} occurrences, got {count}: {old!r}")
    text = text.replace(old, new)
path.write_text(text)

ci = Path(".github/workflows/ci.yml")
ci_text = ci.read_text()
old = "      - run: ruff format --diff . && exit 1\n"
new = "      - run: ruff format --check .\n"
if ci_text.count(old) != 1:
    raise RuntimeError("Diagnostic CI line was not present exactly once")
ci.write_text(ci_text.replace(old, new, 1))
