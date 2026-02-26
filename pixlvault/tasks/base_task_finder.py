from abc import ABC, ABCMeta, abstractmethod


class TaskFinderRegistry(ABCMeta):
    registry = {}

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
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
