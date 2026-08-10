import ssl

import certifi


def verified_ssl_context() -> ssl.SSLContext:
    """Return a strict TLS context backed by the agent's current CA bundle."""
    return ssl.create_default_context(cafile=certifi.where())
