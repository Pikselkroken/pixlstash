
# PixlVault In-Memory Task System Design

## Overview
This document describes a Python class-based task orchestration system for PixlVault. Tasks are not persisted in the database, but are in-memory Python objects that operate on the core datatypes defined in `pixlvault/db_models` (e.g., `Picture`, `Face`, `Tag`, `User`). The system supports dependency-aware execution (DAG), extensibility, and robust orchestration, while integrating cleanly with your existing models.

## Core Concepts

### 1. Task
A `Task` is a Python object representing a unit of work (e.g., image processing, feature extraction, tagging). Each task has:
- Unique ID (in-memory, e.g., UUID)
- Type (Python class or string label)
- Status (enum: pending, running, completed, failed, cancelled)
- Parameters (Python dict, referencing db_models objects or IDs)
- Result (optional, Python object or dict)
- Error message (optional)
- Created/started/completed timestamps (in-memory)
- Owner (optional, reference to a `User` model instance)

### 2. Task Dependency (DAG)
Tasks can depend on the completion of other tasks. Dependencies are modeled as a directed acyclic graph (DAG):
- Each task may have zero or more parent tasks (must complete before this task runs)
- Each task may have zero or more child tasks (triggered after this task completes)
- Dependencies are managed in-memory via references (not persisted)

### 3. Task Runner
A `TaskRunner` is a Python orchestrator that manages task execution:
- Maintains a queue or pool of tasks
- Selects tasks with all dependencies completed and status 'pending'
- Runs the task (calls its `run()` method), updating status
- On success, sets status to 'completed' and triggers children
- On failure, sets status to 'failed' and records error
- Supports retries, cancellation, and progress reporting

### 4. Task Types and Extensibility
- Task types are defined as Python classes (subclassing `BaseTask`)
- Each type implements a `run()` method and validation logic
- New task types can be registered dynamically
- Parameters and results are type-specific, but always Python objects or dicts

### 5. Integration with Existing Models
- Tasks operate directly on db_models objects (e.g., `Picture`, `Face`, `Tag`, `User`)
- Task parameters may include references to these objects or their IDs
- Ownership and permissions can be enforced via the `User` model
- Task status and logs are available in-memory for debugging and monitoring

## Example Python Class Definitions

```python
import uuid
from enum import Enum, auto
from typing import Any, Dict, List, Optional
from datetime import datetime

class TaskStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()

class BaseTask:
    def __init__(self, task_type: str, params: Dict[str, Any], owner=None):
        self.id = str(uuid.uuid4())
        self.type = task_type
        self.status = TaskStatus.PENDING
        self.params = params  # May include Picture, Face, Tag, User, etc.
        self.result = None
        self.error = None
        self.created_at = datetime.utcnow()
        self.started_at = None
        self.completed_at = None
        self.owner = owner
        self.parents: List[BaseTask] = []
        self.children: List[BaseTask] = []

    def add_dependency(self, parent_task: 'BaseTask'):
        self.parents.append(parent_task)
        parent_task.children.append(self)

    def can_run(self):
        return all(p.status == TaskStatus.COMPLETED for p in self.parents)

    def run(self):
        self.started_at = datetime.utcnow()
        self.status = TaskStatus.RUNNING
        try:
            # Implement task logic here, using self.params
            self.result = self._run_task()
            self.status = TaskStatus.COMPLETED
        except Exception as e:
            self.error = str(e)
            self.status = TaskStatus.FAILED
        self.completed_at = datetime.utcnow()

    def _run_task(self):
        raise NotImplementedError

class TaskRunner:
    def __init__(self):
        self.tasks: List[BaseTask] = []

    def add_task(self, task: BaseTask):
        self.tasks.append(task)

    def run_all(self):
        # Simple DAG runner: repeatedly run tasks whose dependencies are met
        while any(t.status == TaskStatus.PENDING for t in self.tasks):
            runnable = [t for t in self.tasks if t.status == TaskStatus.PENDING and t.can_run()]
            if not runnable:
                break  # Deadlock or waiting for external completion
            for task in runnable:
                task.run()
```

## Example Usage
- Create a batch of image processing tasks, each depending on the previous, operating on `Picture` objects
- Run a face extraction task, then a quality assessment task that depends on it, passing `Face` and `Quality` objects
- Compose complex workflows by chaining tasks with dependencies

## Conventions
- Use Python classes for all tasks and orchestration
- Use enums for status/type where possible
- Pass db_models objects or IDs as parameters
- Integrate with User model for ownership and permissions
- Keep all orchestration in-memory unless explicit persistence is needed

## Extending the System
- Add new task types by subclassing `BaseTask` and implementing `_run_task()`
- Add new dependency types (e.g., conditional, parallel) as needed
- Integrate with FastAPI or CLI for task submission, status, and results if desired
- Add UI components for task monitoring and management if needed

---
This design leverages PixlVault's existing db_models as the canonical datatypes for all task operations, and supports robust, extensible, and auditable in-memory task orchestration for future features.
