- `pixlstash-cli plugins` now logs a warning when a planned dependency
  falls back to `name==version` pinning because its report carries no
  `download_info.url`. That fallback is the pre-#1177 dependency-confusion
  surface — without the warning, an operator could not tell a safe pinning
  from the unsafe one in install logs. Issue #1223.
