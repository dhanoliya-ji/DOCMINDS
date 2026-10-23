"""
services/connectors
===================
Cloud imports: OAuth connections to Google Drive, OneDrive / SharePoint,
Notion and Confluence, a file / page picker, import into a project, and the
auto-sync that keeps imported documents current.

    base.py          the Provider interface and the data it passes around
    http.py          the only network access: https, allowlisted, public
    crypto.py        token encryption at rest
    google_drive.py  onedrive.py  notion.py  confluence.py   the providers
    service.py       connections, token refresh, import and sync
    scheduler.py     the in-process sync loop for INGESTION_MODE=inline

Everything is off unless CONNECTORS_ENABLED (and, per provider, its client
id and secret) is set -- see core/config.py section 22.
"""

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
