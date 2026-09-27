- A seed node from a custom pack your ComfyUI does not have (rgthree's **Seed**
  and its kin) no longer stops a run. That node only hands a seed to the
  sampler, so PixlStash takes it out and writes the seed into the sampler
  itself, running without the pack. The Run popup says so before you press Run.
- It only does this where the node feeds a single sampler's seed. Shared
  between several samplers, wired into anything else, or holding a "random"
  placeholder rather than a real seed, it still asks you to install the pack.
