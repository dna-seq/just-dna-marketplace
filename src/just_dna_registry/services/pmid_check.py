"""
Online PMID existence check via NCBI E-utilities (the "curl validator").

This is a **registry ops** helper, invoked from the `revalidate --check-pmids` CLI — it makes a
network call, so it deliberately lives here and NOT in `just-dna-format` (the contract libs are
strictly offline). `just-dna-format` does the cheap regex (`extract_pmids`); confirming a PMID
actually resolves is an upgrade/authoring-time gate.
"""

import httpx

_ESUMMARY: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


def verify_pmids(pmids: list[str], *, timeout: float = 30.0) -> dict[str, bool]:
    """Return `{pmid: exists}` for each PMID, via NCBI esummary. Empty input → empty dict.

    A PMID is "exists" when NCBI returns a result block for it without an `error` field. Raises
    `httpx.HTTPError` on a transport/HTTP failure (the caller decides how to surface it).
    """
    unique = list(dict.fromkeys(p for p in pmids if p))
    if not unique:
        return {}
    resp = httpx.get(
        _ESUMMARY,
        params={"db": "pubmed", "id": ",".join(unique), "retmode": "json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    result = resp.json().get("result", {})
    return {p: bool(result.get(p)) and "error" not in result.get(p, {}) for p in unique}
