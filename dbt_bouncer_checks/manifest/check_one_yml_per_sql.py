"""Custom dbt-bouncer check: the 1:1 colocated properties-file rule (3.1).

Section 6 explains why this is a custom check rather than a configuration:
no released tool enforces the rule, and dbt does not care where a properties
file lives.

Section 6.2 records two things about the loader that matter more than the check
itself. The `custom_checks_dir` glob is ``*/*.py``, so this file **must** sit in
a subdirectory -- a check at the top level is never loaded. And an import failure
is a WARNING, not an error: dbt-bouncer skips the check and the run stays green.
3.40 exists because of that: CI asserts the expected check names are registered
and treats loader warnings as errors, so "the check did not load" cannot look
like "the check passed".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dbt_bouncer.check_framework.decorator import check, fail


# `@check` generates a BaseCheck subclass at import time. dbt-bouncer ships no
# type information, so under `mypy --strict` the decorator is untyped; the narrow
# ignore is preferable to relaxing disallow_untyped_decorators package-wide.
@check  # type: ignore[untyped-decorator]
def check_one_yml_per_sql(model: Any) -> None:  # noqa: ANN401
    """Each model must be documented in `<model_name>.yml` beside `<model_name>.sql`."""
    sql_path = Path(str(model.original_file_path))

    if not model.patch_path:
        fail(f"`{model.name}` has no properties file. Create `{sql_path.with_suffix('.yml')}`.")
        return

    # patch_path is formatted `<package>://models/.../<name>.yml`
    yml_path = Path(str(model.patch_path).split("://")[-1])

    if yml_path.name != f"{model.name}.yml":
        fail(
            f"`{model.name}` is documented in `{yml_path.name}`. The 1:1 rule "
            f"requires `{model.name}.yml`; folder-level schema.yml is banned."
        )

    if yml_path.parent != sql_path.parent:
        fail(f"`{model.name}`: `{yml_path}` is not colocated with `{sql_path}`.")
