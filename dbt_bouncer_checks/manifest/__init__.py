"""Custom dbt-bouncer manifest checks.

Section 6.2: the loader globs ``custom_checks_dir/*/*.py``, so a check MUST sit
in a subdirectory -- one at the top level is never loaded, silently.

This file exists so the directory is a real package rather than an implicit
namespace one. It is picked up by that same glob and imported; verified harmless
on dbt-bouncer 3.8.0, where the suite still reports 25 checks passing with it
present. That was worth checking rather than assuming, because an import failure
here is a WARNING that leaves the run green (3.40).
"""
