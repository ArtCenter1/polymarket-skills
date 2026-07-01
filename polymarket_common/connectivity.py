"""
Polymarket Connectivity Support

Configures proxy and SSL bypass for all API calls, enabling usage from
networks that DNS-block polymarket.com (e.g. Taiwan).

Environment variables:
  HTTP_PROXY / HTTPS_PROXY - Proxy URL (e.g. http://127.0.0.1:7890)
  POLYMARKET_SSL_VERIFY   - Set to "false" to disable SSL verification

Usage (add at top of any script, before any API calls):
    import polymarket_common.connectivity  # noqa: F401
"""

import os
import sys
import ssl
import warnings

# ---------------------------------------------------------------------------
# Repo path setup — add the repo root to sys.path so we can import
# polymarket_common regardless of which subdirectory the script lives in.
# ---------------------------------------------------------------------------


def _add_repo_to_path() -> None:
    """Add the repo root to sys.path so ``polymarket_common`` is importable.

    Walks up from ``__file__`` (i.e. this module's location inside
    ``polymarket_common/``) to the repo root (its dirname) and ensures
    it is on ``sys.path``.
    """
    # This file lives at <repo>/polymarket_common/connectivity.py
    # so dirname(__file__) = <repo>/polymarket_common  →  repo root = dirname of that
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)


_add_repo_to_path()

# ---------------------------------------------------------------------------
# Read env vars once at import time
# ---------------------------------------------------------------------------

_proxy_url = (
    os.environ.get("HTTPS_PROXY")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("https_proxy")
    or os.environ.get("http_proxy")
)

_ssl_verify = os.environ.get("POLYMARKET_SSL_VERIFY", "").strip().lower() != "false"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_proxy_url() -> str | None:
    """Return the proxy URL from environment variables, or None."""
    return _proxy_url


def get_ssl_verify() -> bool:
    """Return whether SSL verification is enabled (default: True)."""
    return _ssl_verify


def get_requests_proxies() -> dict | None:
    """Return a ``requests``-compatible proxies dict, or None if no proxy."""
    if _proxy_url:
        return {"http": _proxy_url, "https": _proxy_url}
    return None


def get_requests_kwargs() -> dict:
    """Return extra keyword arguments for ``requests.get/post(..., **kw)``.

    Merges proxy and SSL settings into a single dict you can unpack:
        resp = requests.get(url, **get_requests_kwargs(), timeout=30)
    """
    kw: dict = {}
    if not _ssl_verify:
        kw["verify"] = False
    if _proxy_url:
        kw["proxies"] = {"http": _proxy_url, "https": _proxy_url}
    return kw


# ---------------------------------------------------------------------------
# Patch: urllib (stdlib)
# ---------------------------------------------------------------------------


def _patch_urllib() -> None:
    """Install a global urllib opener with proxy + optional SSL bypass."""
    if not _proxy_url and _ssl_verify:
        return  # nothing to do

    from urllib.request import build_opener, ProxyHandler, HTTPSHandler

    handlers = []

    # Proxy handler
    if _proxy_url:
        handlers.append(ProxyHandler({
            "http": _proxy_url,
            "https": _proxy_url,
        }))

    # SSL bypass handler
    if not _ssl_verify:
        ctx = ssl.SSLContext(protocol=ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(HTTPSHandler(context=ctx))

    opener = build_opener(*handlers)
    # install_opener is a module-level function, not a method on opener
    from urllib import request as _urllib_request
    _urllib_request.install_opener(opener)


# ---------------------------------------------------------------------------
# Patch: requests library
# ---------------------------------------------------------------------------


def _patch_requests() -> None:
    """Monkey-patch ``requests`` to use proxy and disable SSL verify by default.

    This patches the Session.request method so that any ``requests.get/post``
    call picks up proxy and SSL settings unless the caller explicitly passes
    their own values.
    """
    try:
        import requests
    except ImportError:
        return

    if not _proxy_url and _ssl_verify:
        return  # nothing to patch

    if not _ssl_verify:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    _orig_request = requests.Session.request

    def _patched_request(self, method, url, **kwargs):
        if not _ssl_verify and "verify" not in kwargs:
            kwargs["verify"] = False
        if _proxy_url and "proxies" not in kwargs:
            kwargs["proxies"] = {"http": _proxy_url, "https": _proxy_url}
        return _orig_request(self, method, url, **kwargs)

    requests.Session.request = _patched_request


# ---------------------------------------------------------------------------
# Patch: py_clob_client httpx singleton
# ---------------------------------------------------------------------------


def _patch_py_clob_client() -> None:
    """Monkey-patch ``py_clob_client`` httpx.Client singleton for proxy/SSL.

    The module-level ``_http_client = httpx.Client(http2=True)`` in
    ``py_clob_client.http_helpers.helpers`` is created on first import.
    We monkey-patch it after import to use our proxy and SSL settings.

    httpx natively respects HTTP_PROXY / HTTPS_PROXY env vars for proxying,
    but ``verify=False`` must be passed explicitly at Client construction.
    Since the client is already created, we replace it entirely.
    """
    try:
        import httpx
        from py_clob_client.http_helpers import helpers as _hh
    except ImportError:
        return

    if not _proxy_url and _ssl_verify:
        return  # nothing to patch

    build_kwargs: dict = {"http2": True}
    if not _ssl_verify:
        build_kwargs["verify"] = False
    # httpx respects HTTPS_PROXY env var automatically, but passing explicit
    # proxy is more reliable if the var was set after the interpreter started.
    if _proxy_url:
        build_kwargs["proxy"] = _proxy_url

    new_client = httpx.Client(**build_kwargs)
    _hh._http_client = new_client


# ---------------------------------------------------------------------------
# Apply all patches on import
# ---------------------------------------------------------------------------

_patch_urllib()
_patch_requests()
_patch_py_clob_client()
