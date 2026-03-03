# PixlVault

A REST API server for PixlVault

## Development

- Install dependencies: `pip install -e .`
- Run server: `python -m pixlvault.server`

## Tagger Benchmark

- Benchmark tagging throughput on a folder of media:
	- `python scripts/benchmark_tagger.py /path/to/images --limit 256 --runs 3`
- Tune batch/concurrency between runs via env vars:
	- `PIXLVAULT_TAGGER_MAX_CONCURRENT_GPU=96`
	- `PIXLVAULT_TAGGER_MAX_CONCURRENT_CPU=8`
	- `PIXLVAULT_CUSTOM_TAGGER_BATCH=24`

## Image Plugins

- Built-in plugins live in `image-plugins/built-in/`.
- Current built-ins: `colour_filter`, `scaling`, `brightness_contrast`, `blur_sharpen`.
- User plugins live in `image-plugins/user/`.
- Start from the template: `image-plugins/user/plugin_template.py`.
- Copy the template to a new `.py` file in `image-plugins/user/`, then rename class/id and implement `run()`.
- `plugin_template.py` is intentionally ignored by plugin discovery.

## Database Migrations (Alembic)

PixlVault uses Alembic for schema changes. The server runs migrations on startup.

- Set the database URL with `PIXLVAULT_DB_URL` (defaults to `sqlite:///vault.db`).
- Create a new migration after model changes:
	- `python -m alembic revision --autogenerate -m "describe change"`
- Apply migrations manually if needed:
	- `python -m alembic upgrade head`

## Publishing

- Build: `python setup.py sdist bdist_wheel`
- Upload: `twine upload dist/*`
