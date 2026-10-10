import ssl

import certifi


def verified_ssl_context() -> ssl.SSLContext:
    """Return a strict TLS context backed by the agent's current CA bundle."""
    context = ssl.create_default_context(cafile=certifi.where())
    # Include certificates trusted by Windows (e.g. the hospital's TLS proxy),
    # while retaining hostname and chain verification and certifi public roots.
    context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    return context
