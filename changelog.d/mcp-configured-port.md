- **Connect AI agent** no longer writes a dead address into the MCP client's
  configuration. The desktop app serves its window from a port it picks afresh
  on every launch, so the address copied from it stopped resolving as soon as
  PixlStash restarted. `pixlstash-mcp` now reads the port from your server
  configuration instead, and keeps working when you change it.
- When nothing is listening, `pixlstash-mcp` now says so at start-up and
  explains the usual cause, rather than reporting "connection refused" the
  first time the agent asks a question. The desktop app only serves the
  configured port when remote access is switched on.
- The MCP server gained `list_sets`, `list_characters` and `list_projects`, and
  the picture tools can now filter by set, character or project. It also tells
  the agent what it is for when it connects, and to ask PixlStash rather than
  reading the library's database file behind its back - which looks like a
  shortcut and quietly gives wrong answers.
