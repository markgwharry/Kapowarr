# -*- coding: utf-8 -*-

from time import time
from typing import Any, Dict, Union
from urllib.parse import urlencode

from requests.exceptions import RequestException

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import (BrokenClientReason, Constants,
                                      DownloadState, DownloadType)
from backend.base.helpers import Session
from backend.base.logging import LOGGER
from backend.implementations.external_clients import BaseExternalClient
from backend.internals.settings import Settings


class SABnzbd(BaseExternalClient):
    """SABnzbd Usenet download client integration."""

    client_type = 'SABnzbd'
    download_type = DownloadType.USENET

    required_tokens = ('title', 'base_url', 'api_token')

    # SABnzbd status to Kapowarr state mapping
    state_mapping = {
        'Queued': DownloadState.QUEUED_STATE,
        'Paused': DownloadState.PAUSED_STATE,
        'Downloading': DownloadState.DOWNLOADING_STATE,
        'Fetching': DownloadState.DOWNLOADING_STATE,
        'Grabbing': DownloadState.DOWNLOADING_STATE,
        'Verifying': DownloadState.DOWNLOADING_STATE,
        'Repairing': DownloadState.DOWNLOADING_STATE,
        'Extracting': DownloadState.IMPORTING_STATE,
        'Moving': DownloadState.IMPORTING_STATE,
        'Running': DownloadState.IMPORTING_STATE,
        'Completed': DownloadState.IMPORTING_STATE,
        'Failed': DownloadState.FAILED_STATE,
    }

    def __init__(self, client_id: int) -> None:
        super().__init__(client_id)

        self.ssn: Union[Session, None] = None
        self.nzo_ids: Dict[str, Union[int, None]] = {}
        self.settings = Settings()
        return

    def _api_request(
        self,
        mode: str,
        params: Union[Dict[str, Any], None] = None
    ) -> Dict[str, Any]:
        """Make a request to the SABnzbd API.

        Args:
            mode (str): The API mode/action to call.
            params (Dict[str, Any], optional): Additional parameters.

        Returns:
            Dict[str, Any]: The JSON response.

        Raises:
            ClientNotWorking: Can't connect to client.
            CredentialInvalid: API key is invalid.
        """
        if not self.ssn:
            self.ssn = Session()

        request_params = {
            'mode': mode,
            'apikey': self.api_token,
            'output': 'json'
        }
        if params:
            request_params.update(params)

        try:
            response = self.ssn.get(
                f'{self.base_url}/api',
                params=request_params
            )
        except RequestException:
            LOGGER.exception("Can't connect to SABnzbd instance: ")
            raise ClientNotWorking(BrokenClientReason.CONNECTION_ERROR)

        if not response.ok:
            LOGGER.error(
                f"SABnzbd API request failed: {response.status_code} - {response.text}"
            )
            raise ClientNotWorking(BrokenClientReason.NOT_CLIENT_INSTANCE)

        try:
            data = response.json()
        except Exception:
            LOGGER.error(f"Failed to parse SABnzbd response: {response.text}")
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        # Check for API errors
        status_val = data.get('status') if isinstance(data, dict) else None
        error_val = data.get('error') if isinstance(data, dict) else None

        LOGGER.debug(f"SABnzbd API response - mode={mode}, params={params}, status={status_val}, data_keys={list(data.keys()) if isinstance(data, dict) else 'not-dict'}")

        # Special case: when querying queue/history with nzo_ids that don't exist,
        # SABnzbd returns {'status': False, 'nzo_ids': []} - this is not an error,
        # it just means the item isn't there (e.g., already completed/moved to history)
        # Also handle delete operations - if deleting from queue fails, the item may
        # have already moved to history, which is fine
        if mode in ('queue', 'history') and params:
            # For queue/history queries, a status=False just means the item
            # isn't in that location - not an error
            LOGGER.debug(f"SABnzbd queue/history operation - returning data regardless of status")
            return data

        if status_val is False or error_val:
            error_msg = error_val or 'Unknown error'
            if 'API Key' in error_msg or 'apikey' in error_msg.lower():
                LOGGER.error(f"SABnzbd API key invalid: {error_msg}")
                raise CredentialInvalid
            # Ignore "Retry search" errors for duplicate detection
            if 'Retry' in error_msg or 'duplicate' in error_msg.lower():
                LOGGER.warning(f"SABnzbd duplicate/retry: {error_msg}")
                # Return empty data instead of raising
                return data
            LOGGER.error(f"SABnzbd API error: {error_msg} (mode={mode})")
            raise ClientNotWorking(BrokenClientReason.NOT_CLIENT_INSTANCE)

        return data

    def add_download(
        self,
        download_link: str,
        target_folder: str,
        download_name: Union[str, None]
    ) -> str:
        """Add an NZB to SABnzbd's queue.

        Args:
            download_link (str): The NZB URL or file path.
            target_folder (str): The folder to download to.
            download_name (Union[str, None]): Custom name for the download.

        Returns:
            str: The nzo_id of the queued download.
        """
        params: Dict[str, Any] = {
            'name': download_link,
            'cat': Constants.USENET_CATEGORY,
        }

        if download_name:
            params['nzbname'] = download_name

        # Note: SABnzbd doesn't support per-download target folders in the same
        # way as torrent clients. The category determines the folder.
        # For now we rely on post-processing to move files.

        data = self._api_request('addurl', params)

        if not data.get('status') or not data.get('nzo_ids'):
            LOGGER.error(f"Failed to add NZB to SABnzbd: {data}")
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        nzo_id = data['nzo_ids'][0]
        self.nzo_ids[nzo_id] = None
        LOGGER.info(f"Added NZB to SABnzbd with nzo_id: {nzo_id}")
        return nzo_id

    def get_download(self, download_id: str) -> Union[Dict[str, Any], None]:
        """Get the status of a download.

        Args:
            download_id (str): The nzo_id of the download.

        Returns:
            Dict with 'size', 'progress', 'speed', 'state' keys,
            empty dict if not found, None if deleted.
        """
        # First check the queue
        queue_data = self._api_request('queue', {'nzo_ids': download_id})
        queue_slots = queue_data.get('queue', {}).get('slots', [])

        for slot in queue_slots:
            if slot.get('nzo_id') == download_id:
                return self._parse_queue_slot(slot, download_id)

        # Not in queue, check history
        history_data = self._api_request('history', {'nzo_ids': download_id})
        history_slots = history_data.get('history', {}).get('slots', [])

        for slot in history_slots:
            if slot.get('nzo_id') == download_id:
                return self._parse_history_slot(slot, download_id)

        # Not found in queue or history
        if download_id in self.nzo_ids:
            # We added it but it's gone - must have been deleted
            return None
        return {}

    def _parse_queue_slot(
        self,
        slot: Dict[str, Any],
        download_id: str
    ) -> Dict[str, Any]:
        """Parse a queue slot into our standard format."""
        status = slot.get('status', 'Queued')
        state = self.state_mapping.get(status, DownloadState.DOWNLOADING_STATE)

        # Check for stalled/failing downloads
        if status in ('Paused', 'Queued') and slot.get('eta', 'unknown') == 'unknown':
            if self.nzo_ids.get(download_id) is None:
                self.nzo_ids[download_id] = round(time())
            else:
                timeout = self.settings.sv.failing_download_timeout
                if timeout and (
                    time() - (self.nzo_ids[download_id] or 0) > timeout
                ):
                    state = DownloadState.FAILED_STATE
        else:
            self.nzo_ids[download_id] = None

        # SABnzbd reports size in bytes or with units like "1.2 GB"
        size_str = slot.get('mb', '0')
        try:
            size = int(float(size_str) * 1024 * 1024)  # MB to bytes
        except (ValueError, TypeError):
            size = -1

        # Progress is percentage string like "45.2"
        try:
            progress = float(slot.get('percentage', '0'))
        except (ValueError, TypeError):
            progress = 0.0

        # Speed in KB/s
        try:
            speed_str = slot.get('kbpersec', '0')
            speed = float(speed_str) * 1024  # KB/s to B/s
        except (ValueError, TypeError):
            speed = 0.0

        return {
            'size': size,
            'progress': round(progress, 2),
            'speed': speed,
            'state': state
        }

    def _parse_history_slot(
        self,
        slot: Dict[str, Any],
        download_id: str
    ) -> Dict[str, Any]:
        """Parse a history slot into our standard format."""
        status = slot.get('status', 'Completed')

        if status == 'Completed':
            state = DownloadState.IMPORTING_STATE
        elif status == 'Failed':
            state = DownloadState.FAILED_STATE
        else:
            state = self.state_mapping.get(status, DownloadState.IMPORTING_STATE)

        # History items have bytes directly
        try:
            size = int(slot.get('bytes', 0))
        except (ValueError, TypeError):
            size = -1

        return {
            'size': size,
            'progress': 100.0 if status == 'Completed' else 0.0,
            'speed': 0.0,
            'state': state
        }

    def delete_download(self, download_id: str, delete_files: bool) -> None:
        """Remove a download from SABnzbd.

        Args:
            download_id (str): The nzo_id to delete.
            delete_files (bool): Whether to delete downloaded files.
        """
        # Try to delete from queue first
        try:
            self._api_request('queue', {
                'name': 'delete',
                'value': download_id,
                'del_files': 1 if delete_files else 0
            })
        except ClientNotWorking:
            pass

        # Also try to delete from history
        try:
            self._api_request('history', {
                'name': 'delete',
                'value': download_id,
                'del_files': 1 if delete_files else 0
            })
        except ClientNotWorking:
            pass

        if download_id in self.nzo_ids:
            del self.nzo_ids[download_id]

        return

    @staticmethod
    def test(
        base_url: str,
        username: Union[str, None] = None,
        password: Union[str, None] = None,
        api_token: Union[str, None] = None
    ) -> None:
        """Test connection to SABnzbd.

        Args:
            base_url (str): The base URL of the SABnzbd instance.
            username (Union[str, None]): Not used for SABnzbd.
            password (Union[str, None]): Not used for SABnzbd.
            api_token (Union[str, None]): The API key for SABnzbd.

        Raises:
            ClientNotWorking: Can't connect to client.
            CredentialInvalid: API key is invalid.
        """
        if not api_token:
            raise CredentialInvalid

        ssn = Session()

        try:
            response = ssn.get(
                f'{base_url}/api',
                params={
                    'mode': 'version',
                    'apikey': api_token,
                    'output': 'json'
                }
            )
        except RequestException:
            LOGGER.exception("Can't connect to SABnzbd instance for test: ")
            raise ClientNotWorking(BrokenClientReason.CONNECTION_ERROR)

        if response.status_code == 403:
            LOGGER.error("SABnzbd API key invalid")
            raise CredentialInvalid

        if not response.ok:
            LOGGER.error(
                f"SABnzbd test request failed: {response.status_code}"
            )
            raise ClientNotWorking(BrokenClientReason.NOT_CLIENT_INSTANCE)

        try:
            data = response.json()
        except Exception:
            LOGGER.error(f"Failed to parse SABnzbd test response: {response.text}")
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        if data.get('error'):
            error_msg = data.get('error', '')
            if 'API Key' in error_msg:
                raise CredentialInvalid
            raise ClientNotWorking(BrokenClientReason.NOT_CLIENT_INSTANCE)

        # Verify we got a version back
        if not data.get('version'):
            LOGGER.error("SABnzbd did not return version info")
            raise ClientNotWorking(BrokenClientReason.NOT_CLIENT_INSTANCE)

        LOGGER.info(f"SABnzbd connection test successful, version: {data.get('version')}")
        return
