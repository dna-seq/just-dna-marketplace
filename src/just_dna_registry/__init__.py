"""
just-dna-registry — the reference **client** for the annotation module registry.

The default install is client-only (lightweight: httpx + the `just-dna-format` contract), so a
consumer imports the reference client instead of re-implementing the REST calls + integrity
verification::

    from just_dna_registry import RegistryClient

    with RegistryClient("https://module-registry.just-dna.life", token) as mkt:
        mkt.list_modules()
        mkt.download("just-dna-seq", "coronary", "1.0.0", "./coronary")   # verifies integrity

The server (FastAPI app, compiler, storage, admin CLI) is an optional extra —
``pip install just-dna-registry[server]``.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

from just_dna_registry.client import (  # noqa: F401  (public re-exports)
    RegistryClient,
    RegistryError,
    gather_spec_files,
)
from just_dna_registry.installid import (  # noqa: F401
    generate_install_id,
    validate_install_id,
)

try:
    __version__ = _pkg_version("just-dna-registry")
except PackageNotFoundError:  # running from a source tree without an installed dist
    __version__ = "0.0.0+unknown"
