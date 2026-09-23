- A seed node from a custom pack your ComfyUI does not have (rgthree's **Seed**
  and its kin) no longer stops a run. That node only hands a seed to the
  sampler, and PixlStash sets the seed itself, so it takes the node out and runs
  without the pack. The Run popup says so before you press Run.
- It only does this where the node feeds nothing but a sampler's seed. Wired
  into anything else, it still asks you to install the pack.
