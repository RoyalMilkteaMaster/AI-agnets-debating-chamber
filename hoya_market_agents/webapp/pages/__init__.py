"""Server-rendered page assemblers, split by page family."""

from . import components as _components
from . import history_page, live_page, run_page, settings_page


def _publish(module, names=None):
    selected = names if names is not None else (
        name for name in vars(module) if not name.startswith("__")
    )
    for name in selected:
        globals()[name] = getattr(module, name)


_publish(_components)
for _module in (history_page, live_page, run_page, settings_page):
    _publish(_module, _module.__all__)

del _module
