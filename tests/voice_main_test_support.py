"""Restore the application module after a Voice test reloads it in place.

Other test modules may have imported ``backend.app.main`` during discovery.
Reloading changes that shared module object and its app/lifespan handlers, so
restoring environment variables alone does not isolate the next test class.
"""

import importlib
import sys


def reload_main_with_class_cleanup(testcase):
    previous = sys.modules.get("backend.app.main")
    if previous is None:
        module = importlib.import_module("backend.app.main")

        def remove_new_module():
            sys.modules.pop("backend.app.main", None)
            package = sys.modules.get("backend.app")
            if package is not None and getattr(package, "main", None) is module:
                delattr(package, "main")

        testcase.addClassCleanup(remove_new_module)
        return module

    prior_globals = previous.__dict__.copy()

    def restore_previous_module():
        previous.__dict__.clear()
        previous.__dict__.update(prior_globals)

    testcase.addClassCleanup(restore_previous_module)
    return importlib.reload(previous)
