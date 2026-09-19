- **Connect AI agent** no longer writes a dead address into the MCP client's
  configuration. The desktop app serves its window from a port it picks afresh
  on every launch, so the address copied from it stopped resolving as soon as
  PixlStash restarted. `pixlstash-mcp` now reads the port from your server
  configuration instead, and keeps working when you change it.
