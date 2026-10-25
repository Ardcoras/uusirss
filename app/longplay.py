"""Turn Longplay's article RSS into an account-specific audio feed."""

from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import requests


SITE_URL = 'https://www.longplay.fi'
FEED_URL = SITE_URL + '/feed.xml'
API_URL = 'https://admin.longplay.fi/api/v2'
TIMEOUT = 20
ITUNES_NS = 'http://www.itunes.com/dtds/podcast-1.0.dtd'
ATOM_NS = 'http://www.w3.org/2005/Atom'

ElementTree.register_namespace('itunes', ITUNES_NS)
ElementTree.register_namespace('atom', ATOM_NS)


class LongplayAuthenticationError(Exception):
    pass


class LongplayAudioUnavailableError(Exception):
    pass


def _authenticate(session, username, password):
    response = session.post(API_URL + '/oauth/token', data={
        'grant_type': 'password',
        'username': username,
        'password': password,
        'client_id': '987e04d6-5471-4a09-bac4-e9a5e6d47524',
        'client_secret': '1q2w3e',
    }, timeout=TIMEOUT)
    if response.status_code in (400, 401, 403):
        raise LongplayAuthenticationError()
    response.raise_for_status()
    headers = {'Authorization': 'Bearer ' + response.json()['access_token']}

    response = session.get(API_URL + '/user', headers=headers, timeout=TIMEOUT)
    if response.status_code in (401, 403):
        raise LongplayAuthenticationError()
    response.raise_for_status()
    return headers, response.json()['user']['id']


def _fetch_audio(session, node_id, headers, user_id):
    response = session.get(API_URL + '/nodes/' + str(node_id) + '/files',
                           params={'user': user_id}, headers=headers, timeout=TIMEOUT)
    if response.status_code == 401:
        raise LongplayAuthenticationError()
    if response.status_code in (403, 404):
        return None
    response.raise_for_status()
    audio = response.json()['files'].get('audio') or []
    return audio[0] if audio else None


def resolve_longplay_audio(username, password, node_id):
    """Obtain a new CDN signature after checking the account's current access."""
    with requests.Session() as session:
        headers, user_id = _authenticate(session, username, password)
        audio = _fetch_audio(session, node_id, headers, user_id)
        if not audio:
            raise LongplayAudioUnavailableError()
        return audio['url']


def build_longplay_feed(username, password, audio_url_for):
    with requests.Session() as session:
        headers, user_id = _authenticate(session, username, password)

        response = session.get(FEED_URL, timeout=TIMEOUT)
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        channel = root.find('channel')
        if channel is None:
            raise ValueError('Missing RSS channel')

        # Resolve public article URLs to the IDs required by the private files API.
        nodes = {}
        for content_type in ('story', 'sidetone'):
            response = session.get(API_URL + '/nodes', params={
                'type': content_type,
                'fields[]': ['audio|exists'],
            }, timeout=TIMEOUT)
            response.raise_for_status()
            for node in response.json()['entities']:
                nodes[urlsplit(urljoin(SITE_URL, node['url'])).path] = node

        # The upstream self link describes the public article feed, not this feed.
        for link in channel.findall('{' + ATOM_NS + '}link'):
            if link.get('rel') == 'self':
                channel.remove(link)

        for item in channel.findall('item'):
            path = urlsplit(item.findtext('link', '')).path
            node = nodes.get(path)
            if node is None or not node.get('audio', {}).get('exists'):
                channel.remove(item)
                continue

            audio = _fetch_audio(session, node['id'], headers, user_id)
            if not audio:
                channel.remove(item)
                continue

            # Keep the cover as podcast artwork and replace image enclosures.
            for enclosure in item.findall('enclosure'):
                if enclosure.get('type', '').startswith('image/'):
                    ElementTree.SubElement(item, '{' + ITUNES_NS + '}image',
                                           href=enclosure.get('url', ''))
                item.remove(enclosure)
            ElementTree.SubElement(item, 'enclosure', {
                # CDN URLs expire. Resolve a new signature when the player fetches this URL.
                'url': audio_url_for(node['id']),
                'length': str(audio.get('size') or 0),
                'type': 'audio/mpeg',
            })

        return ElementTree.tostring(root, encoding='utf-8', xml_declaration=True)
