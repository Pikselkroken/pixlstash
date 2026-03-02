from abc import ABC, ABCMeta, abstractmethod


class TaskFinderRegistry(ABCMeta):
    registry = {}

    def __new__(cls, name, bases, namespace):
        cls = super().__new__(cls, name, bases, namespace)
        if not name.startswith("Base"):
            TaskFinderRegistry.registry[name] = cls
        return cls


class BaseTaskFinder(ABC, metaclass=TaskFinderRegistry):
    """Base finder that discovers one type of missing work and returns one task."""

    @abstractmethod
    def finder_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def find_task(self):
        raise NotImplementedError

    def max_inflight_tasks(self) -> int:
        return 1

    def on_task_complete(self, task, error) -> None:
        return None
