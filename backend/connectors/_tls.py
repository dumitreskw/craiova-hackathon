"""TLS helpers for sources with incomplete certificate chains.

Some public sites (e.g. politiaromana.ro) serve only their leaf certificate and
omit the intermediate CA. Python's ``certifi`` bundle has roots but not
intermediates, so verification fails even though the chain is valid. The
correct fix is to supply the missing intermediate — **not** to disable
verification. We ship the public intermediate under ``certs/`` and build an
SSL context from certifi + that intermediate, so the full chain verifies
normally.
"""

from __future__ import annotations

import ssl
from functools import lru_cache
from pathlib import Path

import certifi

CERTS_DIR = Path(__file__).parent / "certs"


@lru_cache(maxsize=8)
def context_with_intermediates(*pem_filenames: str) -> ssl.SSLContext:
    """A default SSL context (certifi roots) plus the given intermediate PEMs.

    Verification stays fully enabled; we only add the legitimate intermediate
    certificate that the server failed to send.
    """
    ctx = ssl.create_default_context(cafile=certifi.where())
    for name in pem_filenames:
        path = CERTS_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"missing bundled intermediate: {path}")
        ctx.load_verify_locations(cafile=str(path))
    return ctx
