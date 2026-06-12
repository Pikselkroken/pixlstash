---
name: AMD GPU (ROCm) test report
about: Report whether experimental AMD GPU acceleration works on your card
title: '[ROCm] '
labels: 'experimental, amd-rocm'
assignees: ''

---

Thanks for testing! AMD GPU (ROCm) support is **experimental and unverified** — we
don't own an AMD card, so your report is how we find out if it works. Crash reports
are just as useful as success stories.

**Your hardware**
- GPU model (e.g. Radeon RX 7900 XTX):
- ROCm version (`rocminfo | grep -i version` or your package manager):
- Linux distro + version:
- Kernel version (`uname -r`):
- PixlStash version:

**What happened?**
- [ ] GPU was detected and offered as an upgrade
- [ ] Backend installed successfully
- [ ] App started on the GPU
- [ ] App fell back to CPU
- [ ] It crashed / failed to start

**Throughput (if it ran on the GPU)**
Compared against CPU on the same machine if you can. Useful numbers:
- Image embeddings (CLIP) per minute:
- Captions (Florence-2) per minute:
- Anything else you measured:

**Logs**
Paste any startup-check output or errors here. Even a clean failure is helpful.

**Notes**
Anything else worth mentioning.
