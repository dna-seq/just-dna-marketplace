"""
Named namespace groups behind the webui's listing tabs (all / featured / popular / new / test).

The *membership policy* lives here on the server — not in a consumer — so the webui, the
`registry-client` CLI, and any other client agree on what each tab contains. In particular, which
namespaces count as **test/sandbox** is a server-config regex (`Settings.test_namespace_pattern`),
and those spaces are hidden from every tab except `test`. Rendering the tabs is the frontend's job;
defining what falls in them is not.

Each group is a thin preset over the primitives `search_modules` already supports (a `sort`, the
`featured` flag, and a namespace scope), so this adds policy, not a new query engine.
"""

import re
from typing import Optional

from pydantic import BaseModel, Field

from just_dna_registry.db.repository import Repository


class GroupInfo(BaseModel):
    """A listing group, as advertised by `GET /api/v1/modules/groups` for the UI to render tabs."""

    key: str = Field(description="Value to pass as `?group=`")
    label: str = Field(description="Human tab label")
    description: str = Field(description="What the tab contains")


# The catalog-wide group registry. `key` is the `?group=` value; order is the suggested tab order.
GROUPS: list[GroupInfo] = [
    GroupInfo(key="all", label="All", description="Everything published (test/sandbox spaces excluded)."),
    GroupInfo(key="featured", label="Featured", description="Namespaces curated by the operators."),
    GroupInfo(key="curated", label="Curated", description="Has an owner-highlighted review/audit."),
    GroupInfo(key="popular", label="Popular", description="Most viewed, downloaded, and starred."),
    GroupInfo(key="new", label="New", description="Most recently updated."),
    GroupInfo(key="test", label="Test", description="Sandbox / test namespaces (hidden from other tabs)."),
]
GROUP_KEYS: frozenset[str] = frozenset(g.key for g in GROUPS)


def test_namespaces(repo: Repository, pattern: str) -> list[str]:
    """The published namespaces classified as test/sandbox by the server-config `pattern`."""
    matcher = re.compile(pattern)
    return [ns for ns in repo.distinct_module_namespaces() if matcher.match(ns)]


def group_filters(group: Optional[str], repo: Repository, pattern: str) -> dict[str, object]:
    """Translate a group key into `search_modules` kwargs (sort / featured / namespace scope).

    `test` isolates the test/sandbox spaces; every other group (and the default, `group=None`)
    excludes them. Returns only the keys a group sets, to merge over the caller's explicit filters.
    """
    if group == "test":
        return {"only_namespaces": test_namespaces(repo, pattern)}

    # All non-test views hide the test/sandbox spaces.
    filters: dict[str, object] = {"exclude_namespaces": test_namespaces(repo, pattern)}
    if group == "featured":
        filters["featured"] = True
    elif group == "curated":
        filters["curated_only"] = True
    elif group == "popular":
        filters["sort"] = "popular"
    elif group == "new":
        filters["sort"] = "recent"
    # group in (None, "all") → just the test-exclusion above.
    return filters
