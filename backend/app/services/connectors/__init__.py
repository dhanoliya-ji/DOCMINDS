"""services/connectors Cloud imports: OAuth connections to Google Drive, OneDrive / SharePoint, Notion and..."""
from typing import Dict, Optional

from app.services.connectors.base import Provider
from app.services.connectors.confluence import Confluence
from app.services.connectors.google_drive import GoogleDrive
from app.services.connectors.notion import Notion
from app.services.connectors.onedrive import OneDrive

PROVIDERS: Dict[str, Provider] = {
    provider.id: provider for provider in (GoogleDrive(), OneDrive(), Notion(), Confluence())
}


def get_provider(provider_id: str) -> Optional[Provider]:
    """The provider with this id, or None if there is no such provider."""
    return PROVIDERS.get(provider_id)
