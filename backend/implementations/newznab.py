# -*- coding: utf-8 -*-

"""
Newznab indexer support for Usenet comic searches.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Union
from xml.etree import ElementTree

from requests.exceptions import RequestException

from backend.base.custom_exceptions import (ClientNotWorking, CredentialInvalid,
                                            InvalidKeyValue)
from backend.base.definitions import (BrokenClientReason, ClientTestResult,
                                      Constants)
from backend.base.helpers import Session, normalise_base_url
from backend.base.logging import LOGGER
from backend.internals.db import get_db


@dataclass
class NewznabIndexerData:
    """Data class for a Newznab indexer."""
    id: int
    title: str
    base_url: str
    api_key: str
    enabled: bool

    def todict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'base_url': self.base_url,
            'api_key': self.api_key,
            'enabled': self.enabled
        }


class NewznabIndexer:
    """Represents a single Newznab indexer."""

    def __init__(self, indexer_id: int) -> None:
        """Load an indexer from the database.

        Args:
            indexer_id (int): The ID of the indexer.

        Raises:
            InvalidKeyValue: Indexer not found.
        """
        data = get_db().execute(
            """
            SELECT id, title, base_url, api_key, enabled
            FROM newznab_indexers
            WHERE id = ?
            LIMIT 1;
            """,
            (indexer_id,)
        ).fetchone()

        if not data:
            raise InvalidKeyValue('id', indexer_id)

        self._id = data['id']
        self._title = data['title']
        self._base_url = data['base_url']
        self._api_key = data['api_key']
        self._enabled = bool(data['enabled'])

    @property
    def id(self) -> int:
        return self._id

    @property
    def title(self) -> str:
        return self._title

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def enabled(self) -> bool:
        return self._enabled

    def get_data(self) -> NewznabIndexerData:
        """Get the indexer data as a dataclass."""
        return NewznabIndexerData(
            id=self._id,
            title=self._title,
            base_url=self._base_url,
            api_key=self._api_key,
            enabled=self._enabled
        )

    def update(self, data: Dict[str, Any]) -> None:
        """Update the indexer settings.

        Args:
            data (Dict[str, Any]): Fields to update.

        Raises:
            InvalidKeyValue: Invalid field value.
            ClientNotWorking: Can't connect to indexer.
            CredentialInvalid: API key is invalid.
        """
        title = data.get('title', self._title)
        base_url = data.get('base_url', self._base_url)
        api_key = data.get('api_key', self._api_key)
        enabled = data.get('enabled', self._enabled)

        if not title:
            raise InvalidKeyValue('title', title)
        if not base_url:
            raise InvalidKeyValue('base_url', base_url)
        if not api_key:
            raise InvalidKeyValue('api_key', api_key)

        base_url = normalise_base_url(base_url)

        # Test connection before saving
        NewznabIndexers.test(base_url, api_key)

        get_db().execute(
            """
            UPDATE newznab_indexers
            SET title = ?, base_url = ?, api_key = ?, enabled = ?
            WHERE id = ?;
            """,
            (title, base_url, api_key, enabled, self._id)
        )

        self._title = title
        self._base_url = base_url
        self._api_key = api_key
        self._enabled = enabled

    def delete(self) -> None:
        """Delete this indexer."""
        get_db().execute(
            "DELETE FROM newznab_indexers WHERE id = ?;",
            (self._id,)
        )

    def search(
        self,
        query: str,
        session: Union[Session, None] = None
    ) -> List[Dict[str, Any]]:
        """Search this indexer for comics.

        Args:
            query (str): The search query.
            session (Session, optional): Session to use. Defaults to None.

        Returns:
            List[Dict[str, Any]]: List of search results with keys:
                - title: Release title
                - link: NZB download URL
                - size: File size in bytes
                - pubdate: Publication date
                - indexer: Name of this indexer
                - guid: Unique identifier
        """
        if not self._enabled:
            return []

        ssn = session or Session()

        try:
            response = ssn.get(
                f'{self._base_url}/api',
                params={
                    't': 'search',
                    'cat': Constants.NEWZNAB_COMIC_CATEGORY,
                    'q': query,
                    'apikey': self._api_key,
                    'limit': 100
                },
                timeout=30
            )
        except RequestException:
            LOGGER.warning(f"Failed to connect to indexer {self._title}")
            return []

        if not response.ok:
            LOGGER.warning(
                f"Indexer {self._title} returned status {response.status_code}"
            )
            return []

        return self._parse_response(response.text)

    def _parse_response(self, xml_text: str) -> List[Dict[str, Any]]:
        """Parse the Newznab RSS/XML response.

        Args:
            xml_text (str): The XML response text.

        Returns:
            List[Dict[str, Any]]: Parsed results.
        """
        results = []

        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError:
            LOGGER.warning(f"Failed to parse XML from indexer {self._title}")
            return []

        # Check for errors
        error = root.find('.//error')
        if error is not None:
            code = error.get('code', '')
            description = error.get('description', 'Unknown error')
            LOGGER.warning(
                f"Indexer {self._title} returned error {code}: {description}"
            )
            return []

        # Find all items in the RSS channel
        channel = root.find('channel')
        if channel is None:
            return []

        for item in channel.findall('item'):
            try:
                result = self._parse_item(item)
                if result:
                    results.append(result)
            except Exception as e:
                LOGGER.debug(f"Failed to parse item from {self._title}: {e}")
                continue

        return results

    def _parse_item(self, item: ElementTree.Element) -> Union[Dict[str, Any], None]:
        """Parse a single RSS item.

        Args:
            item (ElementTree.Element): The item element.

        Returns:
            Dict[str, Any] or None: Parsed result or None if invalid.
        """
        title_elem = item.find('title')
        if title_elem is None or not title_elem.text:
            return None

        title = title_elem.text

        # Get the NZB download link from enclosure
        enclosure = item.find('enclosure')
        if enclosure is not None:
            link = enclosure.get('url', '')
            size = int(enclosure.get('length', 0))
        else:
            # Fallback to link element
            link_elem = item.find('link')
            link = link_elem.text if link_elem is not None and link_elem.text else ''
            size = 0

        if not link:
            return None

        # Get guid
        guid_elem = item.find('guid')
        guid = guid_elem.text if guid_elem is not None and guid_elem.text else link

        # Get pubdate
        pubdate_elem = item.find('pubDate')
        pubdate = pubdate_elem.text if pubdate_elem is not None else ''

        # Try to get size from newznab attributes if not in enclosure
        if size == 0:
            # Look for newznab:attr with name="size"
            for attr in item.findall('.//{http://www.newznab.com/DTD/2010/feeds/attributes/}attr'):
                if attr.get('name') == 'size':
                    try:
                        size = int(attr.get('value', 0))
                    except (ValueError, TypeError):
                        pass
                    break

        return {
            'title': title,
            'link': link,
            'size': size,
            'pubdate': pubdate,
            'indexer': self._title,
            'guid': guid
        }


class NewznabIndexers:
    """Manager class for Newznab indexers."""

    @staticmethod
    def test(base_url: str, api_key: str) -> ClientTestResult:
        """Test connection to a Newznab indexer.

        Args:
            base_url (str): The base URL of the indexer.
            api_key (str): The API key.

        Raises:
            ClientNotWorking: Can't connect to indexer.
            CredentialInvalid: API key is invalid.

        Returns:
            ClientTestResult: Test result.
        """
        if not api_key:
            raise CredentialInvalid

        ssn = Session()

        try:
            # Test with caps request which should work with valid API key
            response = ssn.get(
                f'{base_url}/api',
                params={
                    't': 'caps',
                    'apikey': api_key
                },
                timeout=15
            )
        except RequestException:
            LOGGER.exception("Can't connect to Newznab indexer: ")
            raise ClientNotWorking(BrokenClientReason.CONNECTION_ERROR)

        if response.status_code == 401:
            raise CredentialInvalid

        if response.status_code == 429:
            # Rate limited but connection works
            return ClientTestResult({
                'success': True,
                'description': 'Connection successful (rate limited)'
            })

        if not response.ok:
            LOGGER.error(
                f"Newznab indexer test failed: {response.status_code}"
            )
            raise ClientNotWorking(BrokenClientReason.NOT_CLIENT_INSTANCE)

        # Check for error in response
        try:
            root = ElementTree.fromstring(response.text)
            error = root.find('.//error')
            if error is not None:
                code = error.get('code', '')
                description = error.get('description', '')
                if code in ('100', '101', '102'):
                    # API key related errors
                    raise CredentialInvalid
                LOGGER.error(f"Newznab error {code}: {description}")
                raise ClientNotWorking(BrokenClientReason.NOT_CLIENT_INSTANCE)
        except ElementTree.ParseError:
            LOGGER.error("Failed to parse Newznab caps response")
            raise ClientNotWorking(BrokenClientReason.FAILED_PROCESSING_RESPONSE)

        return ClientTestResult({
            'success': True,
            'description': None
        })

    @staticmethod
    def add(
        title: str,
        base_url: str,
        api_key: str,
        enabled: bool = True
    ) -> NewznabIndexer:
        """Add a new Newznab indexer.

        Args:
            title (str): Display name for the indexer.
            base_url (str): The base URL.
            api_key (str): The API key.
            enabled (bool, optional): Whether to enable. Defaults to True.

        Raises:
            InvalidKeyValue: Invalid parameter.
            ClientNotWorking: Can't connect.
            CredentialInvalid: Invalid API key.

        Returns:
            NewznabIndexer: The created indexer.
        """
        if not title:
            raise InvalidKeyValue('title', title)
        if not base_url:
            raise InvalidKeyValue('base_url', base_url)
        if not api_key:
            raise InvalidKeyValue('api_key', api_key)

        base_url = normalise_base_url(base_url)

        # Test connection first
        NewznabIndexers.test(base_url, api_key)

        indexer_id = get_db().execute(
            """
            INSERT INTO newznab_indexers(title, base_url, api_key, enabled)
            VALUES (?, ?, ?, ?);
            """,
            (title, base_url, api_key, enabled)
        ).lastrowid

        return NewznabIndexer(indexer_id)

    @staticmethod
    def get_indexers(enabled_only: bool = False) -> List[NewznabIndexerData]:
        """Get all configured indexers.

        Args:
            enabled_only (bool, optional): Only return enabled indexers.
                Defaults to False.

        Returns:
            List[NewznabIndexerData]: List of indexers.
        """
        query = "SELECT id, title, base_url, api_key, enabled FROM newznab_indexers"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY title, id;"

        results = get_db().execute(query).fetchalldict()
        return [
            NewznabIndexerData(
                id=r['id'],
                title=r['title'],
                base_url=r['base_url'],
                api_key=r['api_key'],
                enabled=bool(r['enabled'])
            )
            for r in results
        ]

    @staticmethod
    def get_indexer(indexer_id: int) -> NewznabIndexer:
        """Get a specific indexer by ID.

        Args:
            indexer_id (int): The indexer ID.

        Returns:
            NewznabIndexer: The indexer.

        Raises:
            InvalidKeyValue: Indexer not found.
        """
        return NewznabIndexer(indexer_id)

    @staticmethod
    def search_all(query: str) -> List[Dict[str, Any]]:
        """Search all enabled indexers.

        Args:
            query (str): The search query.

        Returns:
            List[Dict[str, Any]]: Combined results from all indexers.
        """
        results = []
        indexers = NewznabIndexers.get_indexers(enabled_only=True)

        with Session() as ssn:
            for indexer_data in indexers:
                try:
                    indexer = NewznabIndexer(indexer_data.id)
                    results.extend(indexer.search(query, ssn))
                except Exception as e:
                    LOGGER.warning(
                        f"Failed to search indexer {indexer_data.title}: {e}"
                    )
                    continue

        return results
