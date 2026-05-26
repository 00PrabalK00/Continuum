# Contributing

Continuum is local-first. Contributions should preserve that default and keep
agent-readable output bounded.

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m continuum --help
```

## Pull Request Expectations

- Add focused tests for behavioral changes.
- Do not commit generated `.continuum/` memory or session logs.
- Clearly mark features that are planned but not implemented.
- Avoid changes that make agents load full history by default.
