# Pixelurgy Vault

A REST API server for Pixelurgy Vault, running on localhost:8765.

## Development

- Install dependencies: `pip install -e .`
- Run server: `python -m pixelurgy_vault.server`

## Publishing

- Build: `python setup.py sdist bdist_wheel`
- Upload: `twine upload dist/*`
