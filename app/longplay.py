"""Turn Longplay's article RSS into an account-specific audio feed."""

from html.parser import HTMLParser
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
    def __init__(self, stage, response):
        self.stage = stage
        self.status = response.status_code
        self.oauth_error = None
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        # Never expose arbitrary provider messages, response bodies or tokens.
        error = payload.get('error') if isinstance(payload, dict) else None
        if isinstance(error, str) and error in {
            'invalid_grant', 'invalid_client', 'invalid_request',
            'unauthorized_client', 'unsupported_grant_type', 'access_denied',
        }:
            self.oauth_error = error

        detail = 'upstream HTTP ' + str(self.status)
        if self.oauth_error:
            detail += ', ' + self.oauth_error
        if stage == 'login' and self.oauth_error == 'invalid_grant':
            message = ('Longplay rejected the username or password (' + detail + '). '
                       'URL-encode both credentials, including +, & and #.')
        elif stage == 'login' and self.oauth_error in {'invalid_client', 'unauthorized_client'}:
            message = 'Longplay rejected the app OAuth client (' + detail + ').'
        elif stage == 'account':
            message = 'Longplay login succeeded, but account lookup failed (' + detail + ').'
        elif stage == 'audio':
            message = 'Longplay account lookup succeeded, but audio authorization failed (' + detail + ').'
        else:
            message = 'Longplay authentication failed at ' + stage + ' (' + detail + ').'
        super().__init__(message)


class LongplayAudioUnavailableError(Exception):
    pass


class _DescriptionText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ('p', 'br', 'div', 'li'):
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in ('p', 'div', 'li'):
            self.parts.append(' ')


def _description_text(html):
    parser = _DescriptionText()
    parser.feed(html)
    return ' '.join(''.join(parser.parts).split())


def _set_article_description(item, node):
    text = node.get('text') or {}
    paragraphs = [text.get('lead_part_1'), text.get('lead_part_2')]
    intro = ''.join(part for part in paragraphs if part and _description_text(part))
    description = item.find('description')
    if intro:
        if description is None:
            description = ElementTree.SubElement(item, 'description')
        description.text = intro
    if description is not None and description.text:
        summary = item.find('{' + ITUNES_NS + '}summary')
        if summary is None:
            summary = ElementTree.SubElement(item, '{' + ITUNES_NS + '}summary')
        summary.text = _description_text(description.text)


def _authenticate(session, username, password):
    response = session.post(API_URL + '/oauth/token', data={
        'grant_type': 'password',
        'username': username,
        'password': password,
        'client_id': '987e04d6-5471-4a09-bac4-e9a5e6d47524',
        'client_secret': '1q2w3e',
    }, timeout=TIMEOUT)
    if response.status_code in (400, 401, 403):
        raise LongplayAuthenticationError('login', response)
    response.raise_for_status()
    headers = {'Authorization': 'Bearer ' + response.json()['access_token']}

    response = session.get(API_URL + '/user', headers=headers, timeout=TIMEOUT)
    if response.status_code in (401, 403):
        raise LongplayAuthenticationError('account', response)
    response.raise_for_status()
    return headers, response.json()['user']['id']


def _fetch_audio(session, node_id, headers, user_id):
    response = session.get(API_URL + '/nodes/' + str(node_id) + '/files',
                           params={'user': user_id}, headers=headers, timeout=TIMEOUT)
    if response.status_code == 401:
        raise LongplayAuthenticationError('audio', response)
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
                'fields[]': ['audio|exists', 'text|lead_part_1', 'text|lead_part_2'],
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

            _set_article_description(item, node)

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
