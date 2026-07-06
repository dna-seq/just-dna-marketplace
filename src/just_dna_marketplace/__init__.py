"""
just-dna-marketplace — the reference **client** for the annotation module marketplace.

The default install is client-only (lightweight: httpx + the `just-dna-format` contract), so a
consumer imports the reference client instead of re-implementing the REST calls + integrity
verification::

    from just_dna_marketplace import MarketplaceClient

    with MarketplaceClient("https://module-marketplace.just-dna.life", token) as mkt:
        mkt.list_modules()
        mkt.download("just-dna-seq", "coronary", "1.0.0", "./coronary")   # verifies integrity

The server (FastAPI app, compiler, storage, admin CLI) is an optional extra —
``pip install just-dna-marketplace[server]``.
"""

from just_dna_marketplace.client import (  # noqa: F401  (public re-exports)
    MarketplaceClient,
    MarketplaceError,
    gather_spec_files,
)
from just_dna_marketplace.installid import (  # noqa: F401
    generate_install_id,
    validate_install_id,
)
