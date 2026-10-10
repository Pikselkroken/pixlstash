- Workflows can be cloned onto one of your workflow sets: Clone onto a
  workflow set… (in a workflow's menu, and beside Edit LoRAs… on the Workflow
  tab) shows the sets with their pictures and, before anything is written, each
  loader the clone rewrites. A GGUF set swaps the core loaders for the
  ComfyUI-GGUF ones, with a warning if ComfyUI does not have that node pack
  yet. Where a set holds two VAEs or two text encoders, you can swap which
  loader takes which before cloning. LoRAs are kept on the same base model and removed on another one, and
  Edit LoRAs… can pick new ones for the clone first. Picking files one at a
  time is still there, as Pick files myself.
