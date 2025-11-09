from __future__ import annotations

from typing import Any, Dict, List, Optional


def update_dup_family_counts(
    storage: Dict[str, Dict[str, Any]],
    family_id: Optional[str],
    path: Optional[str],
    *,
    max_examples: int = 3,
) -> None:
    """
    Increment counts for a duplicate family and keep a few sample paths for logs.
    """
    if not family_id:
        return
    entry = storage.setdefault(family_id, {"count": 0, "examples": []})
    entry["count"] += 1
    if path and len(entry["examples"]) < max_examples and path not in entry["examples"]:
        entry["examples"].append(path)


def top_dup_families(
    storage: Dict[str, Dict[str, Any]],
    *,
    k: int = 5,
    min_count: int = 2,
) -> List[Dict[str, Any]]:
    """
    Return the largest duplicate families ordered by member count.
    """
    results: List[Dict[str, Any]] = []
    for family_id, data in storage.items():
        count = int(data.get("count", 0))
        if count < min_count:
            continue
        examples = list(data.get("examples", []))
        results.append(
            {
                "dup_family_id": family_id,
                "count": count,
                "examples": examples,
            }
        )
    results.sort(key=lambda row: row["count"], reverse=True)
    return results[:k]


__all__ = ["update_dup_family_counts", "top_dup_families"]
