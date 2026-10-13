from collections.abc import Callable
from pathlib import Path

from app.config import get_settings
from app.errors import ConnectorError
from app.integrations.base import CMSAdapter, ConfigurationReader, ConfigurationWriter
from app.integrations.browser_cms import BrowserCMSAdapter
from app.integrations.mock_cms import MockCMSAdapter
from app.integrations.secrets import resolve_credentials
from app.models import CMSConnector

AdapterFactory = Callable[[CMSConnector], CMSAdapter]
ReaderFactory = Callable[[CMSConnector], ConfigurationReader]
# (connector, channel, run artifact dir) -> writer
WriterFactory = Callable[[CMSConnector, str, Path], ConfigurationWriter]

# Operations each connector type supports; recorded on the connector row (blueprint §11).
SUPPORTED_OPERATIONS: dict[str, list[str]] = {
    "mock_cms_api": ["inspect_target", "read_configuration", "write_api", "write_browser"],
}


def build_adapter(connector: CMSConnector) -> CMSAdapter:
    settings = get_settings()
    if connector.connector_type == "mock_cms_api":
        return MockCMSAdapter(
            base_url=connector.base_url,
            credentials=resolve_credentials(connector.credential_ref),
            allowed_origins=settings.allowed_cms_origins,
            timeout_seconds=settings.cms_timeout_seconds,
        )
    raise ConnectorError(
        "configuration", f"Unsupported connector type '{connector.connector_type}'."
    )


def _api_adapter(connector: CMSConnector) -> MockCMSAdapter:
    if connector.connector_type != "mock_cms_api":
        raise ConnectorError(
            "configuration", f"Unsupported connector type '{connector.connector_type}'."
        )
    settings = get_settings()
    return MockCMSAdapter(
        base_url=connector.base_url,
        credentials=resolve_credentials(connector.credential_ref),
        allowed_origins=settings.allowed_cms_origins,
        timeout_seconds=settings.cms_timeout_seconds,
    )


def build_reader(connector: CMSConnector) -> ConfigurationReader:
    return _api_adapter(connector)


def build_writer(connector: CMSConnector, channel: str, artifacts_dir: Path) -> ConfigurationWriter:
    if channel == "api":
        return _api_adapter(connector)
    if channel == "browser":
        settings = get_settings()
        return BrowserCMSAdapter(
            base_url=connector.base_url,
            credentials=resolve_credentials(connector.credential_ref),
            allowed_origins=settings.allowed_cms_origins,
            artifacts_dir=artifacts_dir,
            timeout_ms=settings.browser_timeout_ms,
            headless=settings.browser_headless,
        )
    raise ConnectorError("configuration", f"Unknown execution channel '{channel}'.")
