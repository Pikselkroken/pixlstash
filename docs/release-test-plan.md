# PixlStash Release Test Plan

This document defines the checks to perform before each release.
API/integration tests are automated and run via pytest — this plan covers only
what requires a browser, visual inspection, or a live external service.

---

## How to Use

1. Work through each manual item below in order.
2. Mark each item ✅ Pass / ❌ Fail / ⏭ Skip (with reason).
3. A release is only signed off when all non-skipped items pass.

---

## 0. Installation & First Launch

Perform all checks on a **clean machine** (no prior PixlStash install, no existing `vault.db`).

---

### 0.1 Windows — Installer

| Check | Windows |
|-------|---------|
| Download the `.exe` installer from the latest GitHub release page | |
| Double-click the installer and follow the wizard as a normal user — completes to "Finish" with no error dialog | |
| If SmartScreen appears: click **More info** → **Run anyway** — wizard proceeds normally | |
| Open Start Menu → search **PixlStash Server** → click to launch — a console window opens and prints startup logs | |
| Open `http://localhost:9537` in a browser — the login page loads with username/password fields | |
| Enter the default credentials and click **Log in** — the image grid page renders | |
| Click the import button, select one `.jpg` file, confirm import — the picture appears in the grid | |
| Close the console window — open Task Manager and confirm no `pixlstash` or `python` processes remain | |

---

### 0.2 pip + venv

Requires Python 3.10+. Test on all three platforms.

```
python -m venv venv
# Linux/macOS:  source venv/bin/activate
# Windows:      venv\Scripts\activate
pip install pixlstash
pixlstash-server
```

| Check | macOS | Ubuntu 24.04 | Windows |
|-------|-------|--------------|---------|
| `pip install pixlstash` completes with exit code 0 and no red error lines | | | |
| `pixlstash-server` starts; logs show AI models downloading and then "Uvicorn running" | | | |
| Open `http://localhost:9537` — the login page loads | | | |
| Log in with default credentials, click the import button, import one `.jpg` — it appears in the grid | | | |
| Press Ctrl-C in the terminal — server exits cleanly with no Python traceback and no lock files left | | | |

---

### 0.3 Docker — CPU image

Works on Linux, macOS, and Windows (Docker Desktop / Docker Engine required).

```bash
docker run --rm -e PIXLSTASH_HOST=0.0.0.0 -p 9537:9537 \
  ghcr.io/pikselkroken/pixlstash:latest
```

| Check | macOS | Ubuntu 24.04 | Windows |
|-------|-------|--------------|---------|
| `docker run` command above exits pull phase and prints "Uvicorn running" in logs | | | |
| Open `http://localhost:9537` — the login page loads | | | |
| Log in, import one `.jpg` — it appears in the grid | | | |
| Press Ctrl-C — container stops cleanly (no dangling container in `docker ps -a`) | | | |

---

### 0.4 Docker — GPU image (Ubuntu 24.04)

Requires NVIDIA Container Toolkit installed and Docker restarted.  
Verify GPU access first: `docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi`

```bash
mkdir -p ~/Pictures/pixlstash
docker run -d \
  --runtime nvidia \
  --user $(id -u):$(id -g) \
  -e HOME=/home/pixlstash \
  -e NVIDIA_VISIBLE_DEVICES=all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  -e PIXLSTASH_HOST=0.0.0.0 \
  -p 9537:9537 \
  -v ~/Pictures/pixlstash:/home/pixlstash \
  --name pixlstash \
  ghcr.io/pikselkroken/pixlstash:latest-gpu
```

| Check | Ubuntu 24.04 |
|-------|--------------|
| `nvidia-smi` inside the CUDA verification container shows the GPU with no errors | |
| GPU container starts — `docker logs pixlstash` prints "Uvicorn running" with no CUDA errors | |
| `docker logs pixlstash` shows CUDA/GPU inference messages and **no** "CPU fallback" warning | |
| Open `http://localhost:9537` — the login page loads | |
| Log in, import one `.jpg`, wait 30 s — background tagging completes and tags appear on the picture | |

---

### 0.5 From Source

Requires Python 3.10+, Node.js 20+, npm.

```bash
git clone https://github.com/pikselkroken/pixlstash.git && cd pixlstash
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install --upgrade pip && pip install -e .
cd frontend && npm ci && npm run build && cd ..
pixlstash-server
```

| Check | macOS | Ubuntu 24.04 | Windows |
|-------|-------|--------------|---------|
| `pip install -e .` exits with code 0 and no red error lines | | | |
| `npm run build` exits with code 0 and produces `frontend/dist/` | | | |
| `pixlstash-server` starts and `http://localhost:9537` shows the login page | | | |
| Log in, import one `.jpg` — it appears in the grid | | | |

---

## 1. Authentication

### 1.1 Login / Logout
- [ ] Open **Settings** (gear icon in the toolbar) → click **Log out** in the top-right of the dialog header → verify the browser navigates immediately to the login page
- [ ] While logged out, navigate directly to `http://localhost:9537` → verify you are redirected to the login page and the grid is not shown

### 1.2 API Tokens
- [ ] Open **Settings** → **Account Settings** tab → click **Generate new token** → fill in a name → click **Create** → confirm the new token row appears in the token list with the correct name

### 1.3 Session Persistence
- [ ] While logged in with the grid visible, press **F5** to reload the page → verify the grid loads again without being redirected to the login page

---

## 2. Picture Import

- [ ] Drag two or more image files from the OS file manager and drop them anywhere on the image grid — a "Drop files here to import" overlay appears while dragging, and import starts immediately on drop
- [ ] A progress indicator appears showing import status — wait for it to complete
- [ ] The imported pictures appear in the grid without a page reload

---

## 3. Image Grid & Browsing

### 3.1 Grid Display
- [ ] After import: all thumbnails render within 5 s with no broken-image icons
- [ ] Scroll to the bottom of a grid with more than one page of results — new pictures load automatically as you scroll (no manual "load more" needed)
- [ ] Use the column slider/buttons to change the column count from its current value to a different number — the grid reflows immediately without a page reload

### 3.2 Sorting & Filtering
- [ ] Click the **Sort** button → verify the dropdown lists at least: Date, Import date, Smart score, Star rating, Name, File size, Random
- [ ] Select **Date (newest first)** → confirm the grid reorders so the newest picture is in the top-left position

### 3.3 Search
- [ ] Type a search term in the search bar and press Enter — the grid updates to show only matching pictures
- [ ] Click the ✕ (clear) button on the search bar — the grid resets to the full unfiltered view
- [ ] Click the search bar again — previous search terms from this session appear in the history dropdown

### 3.4 Stacks View
- [ ] Locate a stack indicator badge on a thumbnail → click it — the stack expands to show all member pictures
- [ ] Click the stack badge again — the stack collapses back to the leader thumbnail

---

## 4. Picture Detail (ImageOverlay)

- [ ] Click any picture thumbnail — the ImageOverlay opens and shows the full-size image plus the side panel with metadata
- [ ] Press the **→** (right arrow) keyboard key — the next picture in the grid loads in the ImageOverlay
- [ ] Press the **←** (left arrow) keyboard key — the previous picture loads
- [ ] Press **Escape** — the ImageOverlay closes and the grid is visible again without a page reload

---

## 5. Tags

- [ ] Open the ImageOverlay on a picture → click **Add tag** → type a tag name → press Enter — the tag appears immediately in the tag list without a page reload
- [ ] Click the ✕ next to a tag in the ImageOverlay — the tag is removed immediately from the list without a page reload

---

## 6. Star Rating / Quality Score

- [ ] In the grid, hover over a picture — the star overlay appears; click the 3rd star — the rating saves (star stays filled after moving the mouse away)
- [ ] Open the ImageOverlay for the same picture — verify it shows 3 filled stars
- [ ] In the ImageOverlay side panel: scroll to the **Quality** section and verify that blur, noise, and contrast numeric scores are displayed (not blank)

---

## 7. Picture Sets

- [ ] In the sidebar, click a set with a known picture count — the grid shows only pictures in that set
- [ ] Open the ImageOverlay on one of those pictures → click **Remove from set** (or drag the picture out of the set) — confirm the sidebar count for that set decreases by 1

---

## 8. Projects

- [ ] Open the **Projects** view in the sidebar — at least one project is listed
- [ ] Click the project — the grid shows only pictures belonging to that project and the sidebar count matches the grid count

---

## 9. Characters

- [ ] In the sidebar, click a character — the grid shows only pictures assigned to that character
- [ ] Verify the character row shows a thumbnail and the correct picture/face count

---

## 10. Faces

- [ ] Open the ImageOverlay on a picture that has detected faces — the **Faces** section in the side panel shows face crops
- [ ] If face bounding-box overlays are enabled (toggle in toolbar), open a picture with faces — coloured bounding boxes are drawn over the faces in the image

---

## 11. Stacks

- [ ] In the grid with stacks enabled, find a stack — the top-left stack badge shows the member count
- [ ] Click the badge to expand — all member pictures are shown in the correct stacking order

---

## 12. ComfyUI Integration

- [ ] Open **Settings** → **Workflows** tab → enter the ComfyUI server URL → click **Save** → confirm "Connected" status appears
- [ ] In the toolbar, open the ComfyUI menu → select a text-to-image workflow → fill in a prompt → click **Run** — a progress indicator appears and, after completion, the output picture appears in the grid
- [ ] Repeat with an image-to-image workflow: select a source picture → run — output appears and is linked to the source
- [ ] While a workflow is running: click **Abort** — execution stops and the progress indicator disappears

---

## 13. Image Plugins

For each plugin: open the ImageOverlay on a test picture, apply the plugin, confirm the result is visible.

- [ ] **Blur/Sharpen**: apply blur level 5 → the saved picture is visibly blurrier than the original
- [ ] **Brightness/Contrast**: increase brightness by 50 → the saved picture is visibly brighter
- [ ] **Colour Filter**: apply a colour tint → the saved picture shows the colour shift

---

## 14. Tag Predictions

- [ ] Import a new picture and wait for background processing to complete
- [ ] Open the ImageOverlay → scroll to **Tag Predictions** — at least one predicted tag is listed
- [ ] Click **Accept** on a prediction — the tag moves to the confirmed tag list and disappears from predictions
- [ ] Deleting a tag causes it to drop down to prediction IF it had a > 0.0 prediction
---

## 15. Picture Sharing

> **Note:** Picture sharing (share links / public access) is not yet implemented as of this version.
> When the feature is added, add API tests and expand this section with manual browser checks.

---

## 16. Export

- [ ] Select 3 pictures in the grid (click while holding Ctrl/Cmd) → open the **Export** menu in the toolbar → choose **Full resolution** → click **Download ZIP** → verify the downloaded file opens correctly and contains 3 image files
- [ ] In the sidebar, open a project → open the **Export** menu → download a project export ZIP → verify it contains a `project.json` and the expected image assets

---

## 17. Background Task System

- [ ] After importing a batch of 5+ pictures: a task progress overlay or indicator appears in the UI showing live status (percentage or picture count)
- [ ] Wait for processing to complete — open a processed picture in the ImageOverlay and confirm quality metrics (blur, noise, contrast) are populated with numeric values
- [ ] Open a picture that contains faces — the **Faces** section in the side panel shows at least one face crop
- [ ] Open a picture's ImageOverlay → scroll to the **Description** section — a generated caption is present (not blank)

---

## 18. Performance & Stability

- [ ] Import 200+ pictures; after thumbnails load, scroll through the full grid at normal speed — no thumbnails remain broken (grey/placeholder) and the browser does not freeze
- [ ] While a face extraction background task is processing 20+ images: interact with the grid (scroll, sort, open ImageOverlay) — the UI remains responsive with no full-page freeze

---

## Changelog Reference

Update this section after each release noting any test items added, removed, or changed.

| Release | Notes |
|---------|-------|
| (initial) | Plan created; covers all features as of April 2026 |
| v-next | Automated all `[A-py]` items into pytest; this plan now covers manual-only checks |
| v-next | Added §0 cross-platform installation checks (macOS, Ubuntu 24.04, Windows) |
| v-next | Removed WSL2 from all test matrices; expanded all checklist items to step-by-step instructions |
