- Fixed: the server's log no longer repeats how PixlStash was installed on
  every single request. On the desktop app that was one identical line for
  every thumbnail, and a few seconds of scrolling buried everything else in
  the log. It is now said once when the server works it out, and again only if
  the answer actually changes.
