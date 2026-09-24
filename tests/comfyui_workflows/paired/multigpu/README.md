# Paired-format workflows from comfyui-multigpu

The same fifteen workflows in both of ComfyUI's serialisations: `ui/` is the
editor document ComfyUI saves, `api/` is the graph it executes. Each pair shares
a file name. `tests/test_workflow_library.py` keys both halves and asserts they
land on one topology, which is what a workflow pulled from ComfyUI (always
editor format) needs in order to meet the pictures that workflow made (always
API format). See #1440, plan §2.2.

- Source: <https://github.com/pollockjj/ComfyUI-MultiGPU>, commit
  `37a73feda5804045e4f39d8c640905d62a2169d1`. `ui/` is its `example_workflows/`,
  `api/` its `ci/example_workflows_api/`, with the `_api`/`_API` suffix dropped.
  Only workflows that ship in both formats are here.
- Licence: GPL-3.0, as that repository and this one are.
- One edit: a video preview widget's `fullpath` named the author's home
  directory, and reads `/home/me/` here. It is a widget value, so it reaches no
  key.

Vendored rather than fetched so the tests run offline and cannot drift.
