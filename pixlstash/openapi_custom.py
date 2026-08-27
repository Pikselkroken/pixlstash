"""Custom OpenAPI / Scalar reference rendering for the PixlStash API.

Extracted verbatim from ``pixlstash.server`` (Phase 2, §4.1 of the backend
refactor). Owns the Scalar reference page (``render_scalar_html``), the response-
example synthesis, and the ``_install_custom_openapi`` post-processor that wires
bearer auth and curated examples into the generated schema. ``Server`` inherits
``OpenApiMixin`` so the original ``self._install_custom_openapi()`` call site is
unchanged.
"""

import json

from pixlstash.auth import is_auth_excluded_path


_PICTURES_TAG_DESCRIPTION = """\
Browse, search, import, export and edit the pictures and videos in your library - the core of the API.

**Listing.** `GET /pictures` returns matching rows. Page with `offset` + `limit`, order with
`sort` (plus `descending`), and control how much data comes back per row with `fields`:

- `fields=grid` - compact set tuned for thumbnail grids (stack leaders only)
- `fields=id,score,width,height` - only the columns you name
- *(omit `fields`)* - full metadata for every row

Most filters (tags, characters, sets, score ranges, …) are passed as additional query parameters.

### Common workflow

```bash
# 1) Newest 50, compact projection
curl "https://your-pixlstash-host/api/v1/pictures?sort=DATE&limit=50&fields=grid" \\
  -H "Authorization: Bearer YOUR_TOKEN"

# 2) Full metadata for one picture
curl "https://your-pixlstash-host/api/v1/pictures/123/metadata" \\
  -H "Authorization: Bearer YOUR_TOKEN"

# 3) Download the original file
curl "https://your-pixlstash-host/api/v1/pictures/123.jpg" \\
  -H "Authorization: Bearer YOUR_TOKEN" -o 123.jpg

# 4) Set a manual 5-star score
curl -X PATCH "https://your-pixlstash-host/api/v1/pictures/123" \\
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \\
  -d '{"score": 5}'
```

### Sort values (`sort=`)

| Value | Orders by |
| --- | --- |
| `DATE` | Capture / file date |
| `IMPORTED_AT` | Import time |
| `SCORE` | Manual star rating |
| `SMART_SCORE` | Model-predicted aesthetic score |
| `IMAGE_SIZE` | Pixel dimensions |
| `TEXT_CONTENT` | Relevance to a text `query` |
| `CHARACTER_LIKENESS` | Likeness to a chosen character |

Omit `sort` for natural (id) order; add `descending=false` to reverse.

### Exporting (async)

Exports run as a background job - start it, poll status, then download the ZIP:

```bash
task=$(curl -s "https://your-pixlstash-host/api/v1/pictures/export?set_id=7&resolution=original" \\
  -H "Authorization: Bearer YOUR_TOKEN" | jq -r .task_id)

curl "https://your-pixlstash-host/api/v1/pictures/export/status?task_id=$task" \\
  -H "Authorization: Bearer YOUR_TOKEN"

curl "https://your-pixlstash-host/api/v1/pictures/export/download/$task" \\
  -H "Authorization: Bearer YOUR_TOKEN" -o export.zip
```

> **Trash:** `DELETE /pictures/{id}` moves a picture to the *scrapheap* (recoverable via
> `POST /pictures/scrapheap/restore`); `DELETE /pictures/scrapheap` purges it for good.
> Both scrapheap *moves* and *restores* are recorded in the operation log
> (`pictures.scrapheap.move` / `pictures.scrapheap.restore`) and are reversible with
> `POST /operations/undo`. A purge is **not** recorded and cannot be undone.
"""


_CHARACTERS_TAG_DESCRIPTION = """\
Create characters, assign detected faces to them, and search by face likeness.

A *character* groups faces across your library. Assign faces (by `face_ids`, or whole
`picture_ids`) and PixlStash builds a likeness model used for search and scoring.

### Common workflow

```bash
# Create a character
cid=$(curl -s -X POST "https://your-pixlstash-host/api/v1/characters" \\
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \\
  -d '{"name": "Ada"}' | jq -r .id)

# Assign faces from some pictures
curl -X POST "https://your-pixlstash-host/api/v1/characters/$cid/faces" \\
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \\
  -d '{"picture_ids": [123, 124, 125]}'

# Reference pictures + category summary
curl "https://your-pixlstash-host/api/v1/characters/$cid/reference_pictures" \\
  -H "Authorization: Bearer YOUR_TOKEN"
```

Find characters whose faces resemble an image with `POST /characters/likeness-search`, or look
one up by project with `GET /projects/{project_name}/characters/{character_name}`.
"""


_PICTURE_SETS_TAG_DESCRIPTION = """\
Curated, manually-orderable collections of pictures. Create a set, then add, replace or remove members.

### Common workflow

```bash
# Create a set
sid=$(curl -s -X POST "https://your-pixlstash-host/api/v1/picture_sets" \\
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \\
  -d '{"name": "Best of 2026"}' | jq -r .id)

# Bulk-add pictures
curl -X POST "https://your-pixlstash-host/api/v1/picture_sets/$sid/members" \\
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \\
  -d '{"picture_ids": [123, 124, 125]}'

# List members
curl "https://your-pixlstash-host/api/v1/picture_sets/$sid/members" \\
  -H "Authorization: Bearer YOUR_TOKEN"
```

`POST .../members` adds, `PUT .../members` **replaces** the whole membership, and
`DELETE .../members/{picture_id}` removes one. `POST /picture_sets/membership` checks set
membership for many pictures in a single call.
"""


_TAGS_TAG_DESCRIPTION = """\
Read and edit the tags on pictures, and list every tag in the library.

### Common workflow

```bash
# Every distinct tag
curl "https://your-pixlstash-host/api/v1/tags" -H "Authorization: Bearer YOUR_TOKEN"

# Tags on one picture
curl "https://your-pixlstash-host/api/v1/pictures/123/tags" -H "Authorization: Bearer YOUR_TOKEN"

# Add a tag
curl -X POST "https://your-pixlstash-host/api/v1/pictures/123/tags" \\
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \\
  -d '{"tag": "sunset"}'

# Tags for many pictures at once (max 200 ids)
curl -X POST "https://your-pixlstash-host/api/v1/pictures/tags/bulk_fetch" \\
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \\
  -d '{"picture_ids": [123, 124, 125]}'
```

`DELETE /pictures/{id}/tags/{tag_id}` removes one tag; `DELETE /pictures/{id}/tags` clears them all.
"""


_STACKS_TAG_DESCRIPTION = """\
Stacks group related shots (bursts, edits, variants) under one leader, with a manual order.

### Common workflow

```bash
# Create a stack from some pictures
stack=$(curl -s -X POST "https://your-pixlstash-host/api/v1/stacks" \\
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \\
  -d '{"picture_ids": [123, 124, 125]}' | jq -r .id)

# Add a member
curl -X POST "https://your-pixlstash-host/api/v1/stacks/$stack/members" \\
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \\
  -d '{"picture_ids": [126]}'

# Set the display order
curl -X PATCH "https://your-pixlstash-host/api/v1/stacks/$stack/order" \\
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \\
  -d '{"picture_ids": [124, 123, 125, 126]}'
```

Find the stack a picture belongs to with `GET /pictures/{picture_id}/stack`, and move a single
member with `PATCH /stacks/{stack_id}/members/{picture_id}`.
"""


_PROJECTS_TAG_DESCRIPTION = """\
Top-level workspaces that scope characters and picture sets and hold file/URL attachments.
Endpoints accept a project **id or name**.

### Common workflow

```bash
# Create a project
pid=$(curl -s -X POST "https://your-pixlstash-host/api/v1/projects" \\
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \\
  -d '{"name": "Album cover", "description": "2026 shoot"}' | jq -r .id)

# Attach a reference file (multipart)
curl -X POST "https://your-pixlstash-host/api/v1/projects/$pid/attachments" \\
  -H "Authorization: Bearer YOUR_TOKEN" -F "file=@moodboard.pdf"

# ...or bookmark a URL
curl -X POST "https://your-pixlstash-host/api/v1/projects/$pid/attachments/url" \\
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \\
  -d '{"url": "https://example.com/ref", "title": "Reference"}'

# Export the whole project as a ZIP
curl "https://your-pixlstash-host/api/v1/projects/$pid/export" \\
  -H "Authorization: Bearer YOUR_TOKEN" -o project.zip
```

`GET /projects/{id_or_name}/summary` returns the picture count; the nested
`/picture_sets` lists the sets scoped to the project.
"""


_SNAPSHOTS_TAG_DESCRIPTION = """\
Point-in-time snapshots of the **metadata database** - tags, scores, faces, descriptions, and how
everything is organised. The image files themselves are never copied. Snapshots are taken
automatically on a grandfather-father-son schedule (daily / weekly / monthly) and can also be
created by hand, then restored wholesale or for just the resources you choose.

### Common workflow

```bash
# List restore points (newest first)
curl "https://your-pixlstash-host/api/v1/snapshots" \\
  -H "Authorization: Bearer YOUR_TOKEN"

# Take a manual snapshot
cp=$(curl -s -X POST "https://your-pixlstash-host/api/v1/snapshots" \\
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \\
  -d '{"label": "before big cleanup"}' | jq -r .id)

# Preview exactly what a full restore would change (nothing is written)
curl "https://your-pixlstash-host/api/v1/snapshots/$cp/restore/preview" \\
  -H "Authorization: Bearer YOUR_TOKEN"

# Restore everything (a safety snapshot is taken first)
curl -X POST "https://your-pixlstash-host/api/v1/snapshots/$cp/restore" \\
  -H "Authorization: Bearer YOUR_TOKEN"

# ...or restore just the pictures you select
curl -X POST "https://your-pixlstash-host/api/v1/snapshots/$cp/restore/batch" \\
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \\
  -d '{"resources": [{"type": "picture", "id": 8123}]}'
```

All snapshot endpoints require an **unscoped owner token**. Retention keeps the most recent
7 daily, 4 weekly, and 12 monthly snapshots automatically.
"""


_AUTH_TAG_DESCRIPTION = """\
Session login for the web UI. **For API access, prefer a personal token** (see *Authentication*
at the top) and send it as `Authorization: Bearer …` on every request.

- `POST /login` - authenticate a username/password and set a session cookie.
- `GET /login` - report whether first-run registration is still needed.
- `GET /check-session` - validate the current session or token.
- `POST /logout` - end the session.
"""


_SERVER_TAG_DESCRIPTION = """\
Server status. `GET /version` is public (no token) and returns the running version and install
type - handy as a health / compatibility check:

```bash
curl "https://your-pixlstash-host/version"
```
"""


API_OPENAPI_TAGS = [
    {
        "name": "pictures",
        "description": _PICTURES_TAG_DESCRIPTION,
    },
    {
        "name": "characters",
        "description": _CHARACTERS_TAG_DESCRIPTION,
    },
    {
        "name": "picture_sets",
        "description": _PICTURE_SETS_TAG_DESCRIPTION,
    },
    {
        "name": "tags",
        "description": _TAGS_TAG_DESCRIPTION,
    },
    {
        "name": "stacks",
        "description": _STACKS_TAG_DESCRIPTION,
    },
    {
        "name": "projects",
        "description": _PROJECTS_TAG_DESCRIPTION,
    },
    {
        "name": "snapshots",
        "description": _SNAPSHOTS_TAG_DESCRIPTION,
    },
    {
        "name": "auth",
        "description": _AUTH_TAG_DESCRIPTION,
    },
    {
        "name": "server",
        "description": _SERVER_TAG_DESCRIPTION,
    },
]


API_DESCRIPTION = """\
<a href="https://pixlstash.dev" target="_blank" rel="noopener">
  <img
    src="scalar-assets/logo.png"
    alt="PixlStash"
    width="120"
    style="float: right; margin: 0 0 16px 24px"
  />
</a>

# Simplify your image workflow

**PixlStash is a self-hosted, open-source image library for creators.** It imports your
pictures and videos, auto-tags and captions them with local AI models, recognises
characters and faces, scores image quality, runs natural-language semantic search and
drives ComfyUI workflows - all on your own hardware, with no cloud and no lock-in.

**Integrate with scripts, pipelines and external tools** - fetch images, metadata, tags and
more. This REST API exposes everything the app can do - **pictures, tags, stacks, sets,
characters and projects** - so you can script imports, build integrations and automate your pipeline.

### → Learn more and download at **[pixlstash.dev](https://pixlstash.dev)**

<div style="clear: both"></div>

---

## What can you do with this?

Drive any tool or pipeline you can script. For a worked example, see the
[**PixlStash LM Studio plugin**](https://github.com/pikselkroken/pixlstash-lmstudio) -
it uses this API to let a locally-running LLM illustrate its chat replies with
matching pictures from your PixlStash library.

## Quick start

Create an API token (steps below), then fetch your first 50 pictures - replace
`your-pixlstash-host` with your server's address:

```bash
curl "https://your-pixlstash-host/api/v1/pictures?limit=50" \\
  -H "Authorization: Bearer YOUR_TOKEN"
```

## Authentication

Every request authenticates with a personal **API token**, sent as a Bearer header:

```http
Authorization: Bearer YOUR_TOKEN
```

Tokens have one of two access types:

| Access type | Can do | Notes |
| --- | --- | --- |
| **Full access** | Read **and** write - everything your account can do | Never put a full-access token in a URL |
| **Read-only** | `GET` requests only | May also be passed as a `?token=…` query parameter (handy for share links) |

## Creating a token

**1. Open Settings** - click the gear icon in the top toolbar.

![Open Settings from the top toolbar](scalar-assets/WhereIsUserSettings.jpg)

**2. Open the *Account Settings* tab.**

![The Account Settings tab](scalar-assets/ScreenshotsUserSettings.jpg)

**3. Create the token** - in the **API Tokens** section, type a description, choose an
access type (*Full access* or *Read-only*), optionally tick *Apply watermark*, then click
**Create Token**.

![The API Tokens section in Account Settings](scalar-assets/ScreenshotTokens.jpg)

**4. Copy it now** - the token is shown **only once**. Copy it and store it somewhere safe;
you won't be able to see it again.

![Copy the newly created token](scalar-assets/ScreenshotToken.jpg)

## Using the token

The API is served under `/api/v1`. Send your token in the `Authorization` header on every
request, replacing `your-pixlstash-host` with the address of your PixlStash server. To
confirm a token is valid:

```bash
curl https://your-pixlstash-host/api/v1/check-session \\
  -H "Authorization: Bearer YOUR_TOKEN"
```

A **read-only** token may instead be passed in the query string for quick read requests
(never do this with a full-access token):

```bash
curl "https://your-pixlstash-host/api/v1/pictures?limit=50&token=YOUR_READ_TOKEN"
```

Browse the endpoints below for full request and response details.
"""


# Scalar theme overrides matching the PixlStash dark UI (see frontend/src/main.js).
# Custom properties carry !important so they win over Scalar's own ``.dark-mode``
# rules regardless of stylesheet load order.


_SCALAR_THEME_CSS = """\
    <style>
      /* With the developer-tools toolbar disabled the top header strip is
         empty, but Scalar still reserves its height - collapse it so the
         content title sits at the top of the page. */
      :root,
      .scalar-app,
      .dark-mode {
        --scalar-header-height: 0px !important;
      }
      /* Every Scalar ``.section`` carries generous vertical padding to space
         sections apart; on the first one (the introduction) that top padding
         becomes ~90px of dead space above the title. Trim it to 12px for the
         intro only (matching the sidebar's top inset; other sections keep
         their spacing), and tighten the section's ``gap-12`` flex gap. */
      .scalar-app section.introduction-section {
        padding-top: 12px !important;
        gap: 1rem !important;
      }
      .scalar-app section.introduction-section > .section-content {
        padding-top: 0 !important;
        margin-top: 0 !important;
      }
      /* The main column wrapper ships ``padding: 0 60px``, leaving a wide gap
         between the sidebar and the content. Tighten the horizontal inset. */
      .scalar-app .section-container {
        padding-left: 20px !important;
        padding-right: 20px !important;
      }
      .dark-mode {
        --scalar-font: 'Space Grotesk', ui-sans-serif, system-ui, sans-serif !important;
        --scalar-font-code: 'IBM Plex Mono', ui-monospace, monospace !important;
        --scalar-background-1: #2a2f36 !important;
        --scalar-background-2: #2b3138 !important;
        --scalar-background-3: #313337 !important;
        --scalar-background-accent: rgba(142, 166, 4, 0.16) !important;
        --scalar-color-1: #f2e5da !important;
        --scalar-color-2: rgba(242, 229, 218, 0.72) !important;
        --scalar-color-3: rgba(242, 229, 218, 0.5) !important;
        --scalar-color-accent: #8ea604 !important;
        --scalar-border-color: #3a4047 !important;
      }
      .dark-mode .sidebar {
        --scalar-sidebar-background-1: #1f2328 !important;
        --scalar-sidebar-color-1: #f2e5da !important;
        --scalar-sidebar-color-2: rgba(242, 229, 218, 0.7) !important;
        --scalar-sidebar-border-color: #3a4047 !important;
        --scalar-sidebar-item-hover-background: rgba(255, 255, 255, 0.06) !important;
        --scalar-sidebar-item-hover-color: #f2e5da !important;
        --scalar-sidebar-item-active-background: rgba(142, 166, 4, 0.16) !important;
        --scalar-sidebar-color-active: #8ea604 !important;
        --scalar-sidebar-search-background: #2b3138 !important;
        --scalar-sidebar-search-border-color: #3a4047 !important;
        --scalar-sidebar-search--color: rgba(242, 229, 218, 0.6) !important;
      }
      /* Float the logo (rendered from the OpenAPI description) so the intro
         heading and paragraphs flow around it - Scalar's markdown sanitizer
         strips inline style attributes, so we target the image by src here. */
      img[src$="scalar-assets/logo.png"] {
        float: right !important;
        margin: 0 0 16px 24px !important;
        max-width: 120px;
      }
      /* Make the markdown divider (`---`) end the logo's float so the Quick
         start block never wraps beside it. */
      hr {
        clear: both !important;
      }
    </style>"""


def render_scalar_html(
    spec_url: str,
    default_server: "str | None" = None,
    default_token: "str | None" = None,
) -> str:
    """Return the Scalar API-reference page wired to *spec_url*, forced to the
    PixlStash dark theme.

    Shared by the live ``/scalar`` route and the static docs generator so both
    stay in sync. *spec_url* is a trusted internal literal (``/openapi.json`` for
    the live server, ``openapi.json`` for the published per-version page).

    *default_server* / *default_token* are used only by the published static
    docs: they point Scalar's interactive client at the public demo server and
    prefill its read-only token, so the online reference can run read requests
    out of the box. The live ``/scalar`` route omits both, keeping the
    same-origin ``/`` server and no prefilled credentials. (The demo server must
    allow the docs origin in its ``cors_origins`` for those requests to
    succeed.)
    """
    config = {
        "forceDarkModeState": "dark",
        "hideDarkModeToggle": True,
        "hideModels": True,
        "persistAuth": True,
        "showDeveloperTools": "never",
        "authentication": {"preferredSecurityScheme": "bearerAuth"},
    }
    if default_server:
        config["servers"] = [
            {"url": default_server, "description": "PixlStash demo server"}
        ]
    if default_token:
        config["authentication"]["securitySchemes"] = {
            "bearerAuth": {"token": default_token}
        }
    # JSON uses double quotes, so it embeds cleanly in the single-quoted
    # attribute; neutralise any stray apostrophe so it can't terminate it early.
    config_attr = json.dumps(config).replace("'", "&#39;")
    return f"""<!doctype html>
<html lang="en">
  <head>
    <title>PixlStash API Reference</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap"
      rel="stylesheet"
    />
{_SCALAR_THEME_CSS}
  </head>
  <body>
    <script
      id="api-reference"
      data-url="{spec_url}"
      data-configuration='{config_attr}'
    ></script>
    <!-- Pinned to a specific version so a future @scalar release cannot
         change the bundle served to this self-hosted docs page without an
         intentional bump. Revisit each release; consider vendoring under
         data/scalar-assets/ for SRI/offline guarantees. -->
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.32"></script>
  </body>
</html>
"""


def _example_for_schema(schema, schemas, seen=()):
    """Best-effort representative example value for an OpenAPI schema.

    Most response models are Pydantic ``Optional[...]`` fields, which serialize
    as ``anyOf: [T, null]`` with no example. Scalar then renders the whole
    response example as ``null`` - useless in the docs. We synthesize a
    shape-correct example (picking the non-null branch, recursing through
    ``$ref``/objects/arrays) so every endpoint shows its response structure.

    Returns ``None`` when nothing meaningful can be produced (e.g. a circular
    ref or an empty schema); callers skip injecting an example in that case.
    """
    if not isinstance(schema, dict):
        return None

    ref = schema.get("$ref")
    if ref:
        name = ref.split("/")[-1]
        if name in seen:  # circular reference - stop descending
            return None
        return _example_for_schema(schemas.get(name, {}), schemas, seen + (name,))

    if "example" in schema:
        return schema["example"]
    examples = schema.get("examples")
    if isinstance(examples, list) and examples:
        return examples[0]
    if "default" in schema:
        return schema["default"]
    enum = schema.get("enum")
    if enum:
        return enum[0]

    for combinator in ("anyOf", "oneOf"):
        for sub in schema.get(combinator, []):
            if isinstance(sub, dict) and sub.get("type") == "null":
                continue
            value = _example_for_schema(sub, schemas, seen)
            if value is not None:
                return value

    if "allOf" in schema:
        merged = {}
        for sub in schema["allOf"]:
            value = _example_for_schema(sub, schemas, seen)
            if isinstance(value, dict):
                merged.update(value)
        return merged or None

    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        return {
            key: _example_for_schema(prop, schemas, seen)
            for key, prop in schema.get("properties", {}).items()
        }
    if schema_type == "array":
        item = _example_for_schema(schema.get("items", {}), schemas, seen)
        return [item] if item is not None else []
    if schema_type == "string":
        return {
            "date-time": "2026-01-01T00:00:00Z",
            "date": "2026-01-01",
            "uuid": "00000000-0000-0000-0000-000000000000",
            "email": "user@example.com",
            "binary": "",
        }.get(schema.get("format"), "string")
    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0.0
    if schema_type == "boolean":
        return True
    return None


def _inject_response_examples(operation, schemas):
    """Attach a synthesized ``example`` to an operation's 2xx JSON responses.

    Media types that already carry an ``example``/``examples`` are left alone so
    any hand-authored example always wins.
    """
    for code, response in operation.get("responses", {}).items():
        if not str(code).startswith("2"):
            continue
        for media in response.get("content", {}).values():
            if "example" in media or "examples" in media:
                continue
            media_schema = media.get("schema")
            if not media_schema:
                continue
            try:
                example = _example_for_schema(media_schema, schemas)
            except Exception:
                example = None
            if example is not None:
                media["example"] = example


def _strip_query_param_defaults(operation):
    """Drop ``default`` from an operation's query-parameter schemas.

    FastAPI serialises every optional query param's Python default into its
    OpenAPI schema, and Scalar pre-fills the "try it" example URL with all of
    them - so a plain ``GET /pictures`` renders as
    ``?limit=<MAXINT>&offset=0&descending=true&...``. That just restates the
    defaults, so we remove them from the published schema. Runtime is
    unaffected (FastAPI applies the Python default regardless), and any curated
    per-param ``examples`` are preserved.
    """
    for param in operation.get("parameters", []):
        if param.get("in") != "query":
            continue
        param_schema = param.get("schema")
        if not isinstance(param_schema, dict):
            continue
        param_schema.pop("default", None)
        # Optional[...] params render as ``anyOf: [T, null]``; clear any default
        # tucked into a branch too.
        for branch in param_schema.get("anyOf", []):
            if isinstance(branch, dict):
                branch.pop("default", None)


# Sensible sample values for common path-parameter names, so the docs' example
# URLs read naturally (e.g. ``/pictures/1.jpg`` instead of ``/pictures/1.ext``).
# Anything not listed falls back to a type-based default (1 / "example").
_PATH_PARAM_EXAMPLES = {
    "ext": "jpg",
    "resource_type": "picture",
}


def _inject_path_param_examples(operation):
    """Give every path parameter an example value.

    Without one, Scalar can't fill the ``{name}`` template and renders the
    literal placeholder URL-encoded into the example request - e.g.
    ``/api/v1/pictures/%7Bid%7D.%7Bext%7D``. We set a sample (by known name, then
    by type) so the example URLs are valid. Curated examples are left untouched.
    """
    for param in operation.get("parameters", []):
        if param.get("in") != "path":
            continue
        schema = param.get("schema")
        if not isinstance(schema, dict):
            continue
        if (
            param.get("example") is not None
            or param.get("examples")
            or "example" in schema
            or schema.get("examples")
        ):
            continue
        name = param.get("name", "")
        name_l = name.lower()
        if name in _PATH_PARAM_EXAMPLES:
            value = _PATH_PARAM_EXAMPLES[name]
        elif schema.get("type") in ("integer", "number"):
            value = 1
        elif name_l == "id" or name_l.endswith("_id") or "id_or_name" in name_l:
            # id-like params that are typed as strings still read best as a number.
            value = "1"
        else:
            value = "example"
        # Set both parameter- and schema-level example so whichever Scalar reads
        # when substituting the path template gets a value.
        param["example"] = value
        schema["examples"] = [value]


class OpenApiMixin:
    """OpenAPI schema post-processing for ``Server``."""

    def _install_custom_openapi(self):
        """Post-process the generated OpenAPI schema for the reference UI.

        Two fixes, both stemming from the schema FastAPI emits by default:

        * **Bearer auth** - auth is enforced by middleware, not per-route
          dependencies, so FastAPI declares no ``securitySchemes`` and the docs'
          example code omits the ``Authorization`` header. We declare an HTTP
          Bearer scheme and attach it to every operation that actually requires
          auth (same public-path rules as the middleware).
        * **Response examples** - most response models are Pydantic
          ``Optional[...]`` (``anyOf: [T, null]``) with no example, which Scalar
          renders as a bare ``null``. We synthesize a shape-correct example for
          each 2xx JSON response so endpoints show their actual structure.
        * **Query-parameter defaults** - FastAPI emits each optional query
          param's default into its schema, and Scalar then pre-fills the
          "try it" example URL with every one of them (e.g. ``?limit=<MAXINT>``
          ``&offset=0&descending=true``). That redundant noise just restates
          the defaults, so we drop ``default`` from query-param schemas. Runtime
          is unaffected (FastAPI still applies the Python default); curated
          per-param ``examples`` are preserved.
        """

        build_schema = self.api.openapi

        def custom_openapi():
            if self.api.openapi_schema:
                return self.api.openapi_schema
            schema = build_schema()
            # A server entry lets the reference UI build concrete request URLs
            # for its code samples; without one Scalar renders an empty example.
            # Relative so it resolves against whatever origin serves the docs.
            schema.setdefault("servers", [{"url": "/", "description": "This server"}])
            components = schema.setdefault("components", {})
            components.setdefault("securitySchemes", {})["bearerAuth"] = {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": (
                    "Personal API token from Account Settings → API Tokens. "
                    "Read-only tokens may also be passed as a `?token=` query "
                    "parameter."
                ),
            }
            requirement = [{"bearerAuth": []}]
            all_schemas = components.get("schemas", {})
            http_methods = {"get", "post", "put", "patch", "delete"}
            for path, path_item in schema.get("paths", {}).items():
                public = is_auth_excluded_path(path)
                for method, operation in path_item.items():
                    if method.lower() not in http_methods:
                        continue
                    if not public:
                        operation["security"] = requirement
                    _inject_response_examples(operation, all_schemas)
                    _strip_query_param_defaults(operation)
                    _inject_path_param_examples(operation)

            # Lead the reference with the picture listing (the most useful
            # starting point) by ordering its path first. This is presentation
            # only - it does not affect route matching.
            paths = schema.get("paths", {})
            # Local import avoids a server <-> openapi_custom import cycle
            from pixlstash.server import API_V1_PREFIX

            lead_path = f"{API_V1_PREFIX}/pictures"
            if lead_path in paths:
                schema["paths"] = {
                    lead_path: paths[lead_path],
                    **{p: item for p, item in paths.items() if p != lead_path},
                }
            self.api.openapi_schema = schema
            return schema

        self.api.openapi = custom_openapi
