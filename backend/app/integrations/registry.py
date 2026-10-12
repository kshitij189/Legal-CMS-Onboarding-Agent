from collections.abc import Callable

from app.config import get_settings
from app.errors import ConnectorError
from app.integrations.base import CMSAdapter
from app.integrations.mock_cms import MockCMSAdapter
from app.integrations.secrets import resolve_credentials
from app.models import CMSConnector

AdapterFactory = Callable[[CMSConnector], CMSAdapter]

# Operations each connector type supports; recorded on the connector row (blueprint §11).
SUPPORTED_OPERATIONS: dict[str, list[str]] = {
    "mock_cms_api": ["inspect_target"],
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
