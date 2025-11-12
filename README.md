# PixlVault

A REST API server for PixlVault

## Development

- Install dependencies: `pip install -e .`
- Run server: `python -m pixlvault.server`

## Publishing

- Build: `python setup.py sdist bdist_wheel`
- Upload: `twine upload dist/*`
