"""The reference-folder watcher must never hold its own lock while calling
into watchdog.

watchdog dispatches filesystem events to handlers while holding the observer's
lock, and the handler (`_schedule_rescan`) takes the watcher's lock from inside
that dispatch. If `watch_folder` scheduled a watch under the watcher's lock, a
file event landing at that moment deadlocked both threads - which is exactly
what a library switch did when a watched folder changed during `Vault.start`.
The observer here is a fake with watchdog's lock discipline; the test hangs
with the old code and finishes with the new.
"""

from __future__ import annotations

import threading

from pixlstash.utils import reference_folder_watcher as module
from pixlstash.utils.reference_folder_watcher import ReferenceFolderWatcher


class _LockedObserver:
    """The parts of watchdog's observer the watcher touches, with its lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.scheduled: list[tuple[int, str]] = []
        self.unscheduled: list[object] = []
        self.handlers: list = []
        self.dispatching = threading.Event()
        self.release = threading.Event()

    def schedule(self, handler, path, recursive=False):
        with self._lock:
            self.scheduled.append((handler._folder_id, path))
            self.handlers.append(handler)
            return object()

    def unschedule(self, watch):
        with self._lock:
            self.unscheduled.append(watch)

    def dispatch_like_watchdog(self, folder_id: int) -> None:
        """Hold the observer lock, call the handler, and wait to be released."""
        with self._lock:
            self.dispatching.set()
            self.release.wait(timeout=5)
            handler = _handler_for(self, folder_id)
            handler._callback(folder_id)

    def start(self):
        return None

    def stop(self):
        return None

    def join(self):
        return None


def _handler_for(observer: _LockedObserver, folder_id: int):
    return next(h for h in observer.handlers if h._folder_id == folder_id)


def _watcher_with(observer: _LockedObserver) -> ReferenceFolderWatcher:
    watcher = ReferenceFolderWatcher(on_folder_changed=lambda folder_id: None)
    # The real observer thread was never started; it is simply replaced.
    watcher._observer = observer
    return watcher


def test_a_file_event_during_watch_scheduling_does_not_deadlock(tmp_path):
    observer = _LockedObserver()
    watcher = _watcher_with(observer)
    watcher.watch_folder(1, str(tmp_path))

    # A dispatch thread holds the observer lock and is about to call our
    # handler, which takes the watcher lock...
    dispatcher = threading.Thread(
        target=observer.dispatch_like_watchdog, args=(1,), daemon=True
    )
    dispatcher.start()
    assert observer.dispatching.wait(timeout=5)

    # ...while the main thread schedules another watch, which used to take the
    # watcher lock first and then wait for the observer lock: the ABBA pair.
    scheduler = threading.Thread(
        target=watcher.watch_folder, args=(2, str(tmp_path)), daemon=True
    )
    scheduler.start()
    observer.release.set()

    scheduler.join(timeout=5)
    dispatcher.join(timeout=5)
    assert not scheduler.is_alive(), "watch_folder deadlocked against the dispatch"
    assert not dispatcher.is_alive(), "the dispatch deadlocked against watch_folder"
    assert [f for f, _ in observer.scheduled] == [1, 2]

    for timer in list(watcher._timers.values()):
        timer.cancel()


def test_unwatch_releases_the_lock_before_calling_the_observer(tmp_path):
    observer = _LockedObserver()
    watcher = _watcher_with(observer)
    watcher.watch_folder(3, str(tmp_path))

    dispatcher = threading.Thread(
        target=observer.dispatch_like_watchdog, args=(3,), daemon=True
    )
    dispatcher.start()
    assert observer.dispatching.wait(timeout=5)

    unwatcher = threading.Thread(target=watcher.unwatch_folder, args=(3,), daemon=True)
    unwatcher.start()
    observer.release.set()

    unwatcher.join(timeout=5)
    dispatcher.join(timeout=5)
    assert not unwatcher.is_alive()
    assert not dispatcher.is_alive()
    assert len(observer.unscheduled) == 1
    assert module is not None
