"""HTTPS origin validation shared by CMS access and existing binding flows."""

from pydantic import HttpUrl, TypeAdapter, ValidationError

_http_url_adapter = TypeAdapter(HttpUrl)


def canonicalize_origin(origin: str) -> str:
    """Validate and normalize an HTTPS browser origin for exact comparisons."""
    try:
        url = _http_url_adapter.validate_python(origin)
    except ValidationError as error:
        raise ValueError("CMS origin must be a valid HTTPS origin.") from error

    if (
        url.scheme != "https"
        or url.username is not None
        or url.password is not None
        or url.path not in ("", "/")
        or url.query is not None
        or url.fragment is not None
    ):
        raise ValueError(
            "CMS origin must be an HTTPS origin without credentials or URL components."
        )

    host = url.host
    if host is None:
        raise ValueError("CMS origin must include a host.")
    port_suffix = "" if url.port in (None, 443) else f":{url.port}"
    return f"https://{host}{port_suffix}"
