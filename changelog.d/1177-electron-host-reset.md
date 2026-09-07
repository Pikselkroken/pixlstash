- Fixed: turning remote access off now puts the server back on this machine
  only. Turning it on had written "listen on every network interface" into the
  settings file and turning it off left that line behind, so if you later
  started PixlStash from the command line instead of the desktop app, it came
  up reachable from the network you had switched off.
