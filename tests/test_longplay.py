import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

import requests

from app import app
from app.longplay import API_URL, FEED_URL, ITUNES_NS, TIMEOUT


SOURCE_FEED = b'''<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>
  <title>Long Play</title><link>https://www.longplay.fi/feed.xml</link>
  <description>Articles</description><language>fi</language>
  <atom:link href="https://www.longplay.fi/feed.xml" rel="self"/>
  <item><title>Recording &amp; story</title>
    <link>https://www.longplay.fi/pitkat/recording</link><guid>stable-guid</guid>
    <description><![CDATA[<p>Article summary</p>]]></description>
    <pubDate>Fri, 16 Oct 2026 21:01:45 GMT</pubDate>
    <enclosure url="https://cdn.longplay.fi/cover.jpg" length="0" type="image/jpeg"/>
  </item>
  <item><title>Text only</title><link>https://www.longplay.fi/kulttuuri/text</link></item>
  <item><title>Not purchased</title><link>https://www.longplay.fi/pitkat/denied</link></item>
  <item><title>No files</title><link>https://www.longplay.fi/pitkat/empty</link></item>
  <item><title>Short article</title><link>https://www.longplay.fi/lyhyet/short</link></item>
  <item><title>Unknown article</title><link>https://www.longplay.fi/pitkat/unknown</link></item>
</channel></rss>'''


def upstream_response(payload=None, status=200, content=None):
    response = requests.Response()
    response.status_code = status
    response._content = content if content is not None else json.dumps(payload).encode()
    return response


class LongplayFeedTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.session_patch = patch('app.longplay.requests.Session')
        self.session = self.session_patch.start().return_value.__enter__.return_value
        self.addCleanup(self.session_patch.stop)
        self.session.post.return_value = upstream_response({'access_token': 'account-token'})
        self.denied_status = 403
        self.audio = [{'url': 'https://cdn.longplay.iamweb.co/2026-10/'
                             'LP%20168_Meit%C3%A4%20Ei_Luotu_Sotimaan.mp3'
                             '?Expires=1&KeyName=lp-live-bucket&Signature=expired',
                       'size': 123456}]
        self.article_text = None
        self.session.get.side_effect = self.get_upstream

    def get_upstream(self, url, **kwargs):
        self.assertEqual(kwargs['timeout'], TIMEOUT)
        if url == API_URL + '/user':
            return upstream_response({'user': {'id': '42'}})
        if url == FEED_URL:
            self.assertNotIn('headers', kwargs)
            return upstream_response(content=SOURCE_FEED)
        if url == API_URL + '/nodes':
            self.assertNotIn('headers', kwargs)
            if kwargs['params']['type'] == 'sidetone':
                return upstream_response({'entities': [
                    {'id': '104', 'url': '/lyhyet/short', 'audio': {'exists': False}},
                ]})
            return upstream_response({'entities': [
                {'id': '100', 'url': '/pitkat/recording', 'audio': {'exists': True},
                 'text': self.article_text},
                {'id': '101', 'url': '/kulttuuri/text', 'audio': {'exists': False}},
                {'id': '102', 'url': '/pitkat/denied', 'audio': {'exists': True}},
                {'id': '103', 'url': '/pitkat/empty', 'audio': {'exists': True}},
            ]})
        self.assertEqual(kwargs['headers'], {'Authorization': 'Bearer account-token'})
        self.assertEqual(kwargs['params'], {'user': '42'})
        if url == API_URL + '/nodes/100/files':
            return upstream_response({'files': {'audio': self.audio}})
        if url == API_URL + '/nodes/102/files':
            return upstream_response({}, self.denied_status)
        if url == API_URL + '/nodes/103/files':
            return upstream_response({'files': {'audio': []}})
        self.fail('Unexpected upstream URL: ' + url)

    def fetch_feed(self):
        return self.client.get('/longplay', query_string={
            'username': 'reader@example.com', 'password': 'secret&+password',
        })

    def test_authenticated_feed_preserves_metadata_and_uses_audio_enclosures(self):
        response = self.fetch_feed()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/rss+xml')
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        channel = ElementTree.fromstring(response.data).find('channel')
        self.assertEqual(channel.findtext('language'), 'fi')
        items = channel.findall('item')
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.findtext('title'), 'Recording & story')
        self.assertEqual(item.findtext('link'), 'https://www.longplay.fi/pitkat/recording')
        self.assertEqual(item.findtext('guid'), 'stable-guid')
        self.assertEqual(item.findtext('description'), '<p>Article summary</p>')
        self.assertEqual(item.findtext('pubDate'), 'Fri, 16 Oct 2026 21:01:45 GMT')
        enclosure = item.find('enclosure')
        self.assertEqual(enclosure.get('length'), '123456')
        self.assertEqual(enclosure.get('type'), 'audio/mpeg')
        audio_url = urlsplit(enclosure.get('url'))
        self.assertEqual(audio_url.path, '/longplay/audio/100.mp3')
        self.assertEqual(parse_qs(audio_url.query), {
            'username': ['reader@example.com'], 'password': ['secret&+password'],
        })
        self.assertEqual(item.find('{' + ITUNES_NS + '}image').get('href'),
                         'https://cdn.longplay.fi/cover.jpg')
        self.assertNotIn(b'account-token', response.data)
        self.assertNotIn(b'Signature=', response.data)
        self.assertNotIn(b'Expires=', response.data)
        login = self.session.post.call_args
        self.assertEqual(login.args[0], API_URL + '/oauth/token')
        self.assertEqual(login.kwargs['data']['username'], 'reader@example.com')
        self.assertEqual(login.kwargs['data']['password'], 'secret&+password')

    def test_missing_credentials_do_not_contact_longplay(self):
        for credentials in ({}, {'username': 'reader'}, {'password': 'secret'}):
            with self.subTest(credentials=credentials):
                response = self.client.get('/longplay', query_string=credentials)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.session.post.assert_not_called()

    def test_article_intro_enriches_description_without_extra_requests(self):
        self.article_text = {
            'lead_part_1': '<p>A story about <em>Finland</em> &amp; its people.</p>',
            'lead_part_2': '<p>More background.&nbsp;What happens next?</p>',
        }
        response = self.fetch_feed()
        self.assertEqual(response.status_code, 200)
        item = ElementTree.fromstring(response.data).find('channel/item')
        self.assertEqual(item.findtext('description'), ''.join(self.article_text.values()))
        self.assertEqual(item.findtext('{' + ITUNES_NS + '}summary'),
                         'A story about Finland & its people. More background. What happens next?')
        self.assertEqual(len(item.findall('description')), 1)
        self.assertEqual(self.session.get.call_count, 7)
        index_calls = [call for call in self.session.get.call_args_list
                       if call.args[0] == API_URL + '/nodes']
        self.assertEqual(len(index_calls), 2)
        for call in index_calls:
            self.assertIn('text|lead_part_1', call.kwargs['params']['fields[]'])
            self.assertIn('text|lead_part_2', call.kwargs['params']['fields[]'])

    def test_empty_intro_keeps_original_description_and_adds_podcast_summary(self):
        self.article_text = {'lead_part_1': '<p>&nbsp;</p>', 'lead_part_2': None}
        response = self.fetch_feed()
        item = ElementTree.fromstring(response.data).find('channel/item')
        self.assertEqual(item.findtext('description'), '<p>Article summary</p>')
        self.assertEqual(item.findtext('{' + ITUNES_NS + '}summary'), 'Article summary')

    def test_article_intro_supplies_missing_rss_description(self):
        self.article_text = {'lead_part_2': '<p>Background &amp; context.</p>'}
        original_get = self.get_upstream

        def get_without_description(url, **kwargs):
            if url == FEED_URL:
                return upstream_response(content=SOURCE_FEED.replace(
                    b'<description><![CDATA[<p>Article summary</p>]]></description>', b''))
            return original_get(url, **kwargs)

        self.session.get.side_effect = get_without_description
        response = self.fetch_feed()
        item = ElementTree.fromstring(response.data).find('channel/item')
        self.assertEqual(item.findtext('description'), '<p>Background &amp; context.</p>')
        self.assertEqual(item.findtext('{' + ITUNES_NS + '}summary'), 'Background & context.')

    def test_rejected_login_returns_401_without_exposing_upstream_body(self):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                self.session.post.return_value = upstream_response({'error': 'private details'}, status)
                response = self.fetch_feed()
                self.assertEqual(response.status_code, 401)
                self.assertNotIn(b'private details', response.data)
        self.session.get.assert_not_called()

    def test_missing_file_is_skipped(self):
        self.denied_status = 404
        response = self.fetch_feed()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(ElementTree.fromstring(response.data).findall('channel/item')), 1)

    def test_login_error_identifies_rejected_credentials_without_leaking_details(self):
        self.session.post.return_value = upstream_response({
            'error': 'invalid_grant', 'error_description': 'private password and token details',
        }, 400)
        with self.assertLogs(app.logger, level='WARNING') as logs:
            response = self.fetch_feed()
        self.assertEqual(response.status_code, 401)
        self.assertIn(b'invalid_grant', response.data)
        self.assertIn(b'URL-encode', response.data)
        self.assertNotIn(b'private password', response.data)
        self.assertIn('stage=login', logs.output[0])
        self.assertNotIn('secret&+password', '\n'.join(logs.output))

    def test_login_error_identifies_oauth_client_problem(self):
        self.session.post.return_value = upstream_response({'error': 'invalid_client'}, 401)
        response = self.fetch_feed()
        self.assertIn(b'rejected the app OAuth client', response.data)
        self.assertIn(b'invalid_client', response.data)

    def test_account_error_distinguishes_successful_login(self):
        self.session.get.return_value = upstream_response({}, 403)
        self.session.get.side_effect = None
        response = self.fetch_feed()
        self.assertEqual(response.status_code, 401)
        self.assertIn(b'login succeeded, but account lookup failed', response.data)
        self.assertIn(b'HTTP 403', response.data)

    def test_non_json_login_error_is_safe(self):
        self.session.post.return_value = upstream_response(content=b'<html>private details</html>', status=403)
        response = self.fetch_feed()
        self.assertEqual(response.status_code, 401)
        self.assertIn(b'failed at login', response.data)
        self.assertIn(b'HTTP 403', response.data)
        self.assertNotIn(b'private details', response.data)

    def test_encoded_credentials_reach_login_unchanged(self):
        password = 'test+with&symbols#and%chars'
        response = self.client.get('/longplay', query_string={
            'username': 'reader+rss@example.com', 'password': password,
        })
        self.assertEqual(response.status_code, 200)
        data = self.session.post.call_args.kwargs['data']
        self.assertEqual(data['username'], 'reader+rss@example.com')
        self.assertEqual(data['password'], password)

    def test_expired_token_on_file_request_returns_401(self):
        self.denied_status = 401
        self.assertEqual(self.fetch_feed().status_code, 401)

    def test_no_accessible_audio_produces_valid_empty_feed(self):
        self.audio = None
        response = self.fetch_feed()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ElementTree.fromstring(response.data).findall('channel/item'), [])

    def test_unknown_file_size_defaults_to_zero(self):
        self.audio = [{'url': 'https://cdn.longplay.fi/story.mp3'}]
        response = self.fetch_feed()
        enclosure = ElementTree.fromstring(response.data).find('channel/item/enclosure')
        self.assertEqual(enclosure.get('length'), '0')

    def test_upstream_errors_are_reported(self):
        for status in (429, 500, 503):
            with self.subTest(status=status):
                self.session.post.return_value = upstream_response({}, status)
                self.assertEqual(self.fetch_feed().status_code, 502)

    def test_timeout_returns_504(self):
        self.session.get.side_effect = requests.Timeout()
        self.assertEqual(self.fetch_feed().status_code, 504)

    def test_invalid_upstream_response_returns_502(self):
        for response in (upstream_response({}), upstream_response(content=b'not json')):
            with self.subTest(body=response.content):
                self.session.post.return_value = response
                self.assertEqual(self.fetch_feed().status_code, 502)

    def test_invalid_source_feed_returns_502(self):
        original_get = self.get_upstream

        def get_with_invalid_feed(url, **kwargs):
            if url == FEED_URL:
                return upstream_response(content=b'<rss>')
            return original_get(url, **kwargs)

        self.session.get.side_effect = get_with_invalid_feed
        self.assertEqual(self.fetch_feed().status_code, 502)

    def test_delayed_playback_gets_fresh_signed_url(self):
        response = self.fetch_feed()
        enclosure_url = ElementTree.fromstring(response.data).find(
            'channel/item/enclosure').get('url')
        fresh_url = self.audio[0]['url'].replace('Expires=1', 'Expires=9999999999').replace(
            'Signature=expired', 'Signature=fresh')
        self.audio[0]['url'] = fresh_url
        self.session.get.reset_mock()

        playback = self.client.get(enclosure_url, headers={'Range': 'bytes=1024-'})
        self.assertEqual(playback.status_code, 302)
        self.assertEqual(playback.headers['Location'], fresh_url)
        self.assertEqual(playback.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(playback.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(self.session.post.call_count, 2)
        self.assertEqual([call.args[0] for call in self.session.get.call_args_list], [
            API_URL + '/user', API_URL + '/nodes/100/files',
        ])

    def test_enclosure_stays_the_same_when_cdn_signature_changes(self):
        first = self.fetch_feed()
        self.audio[0]['url'] += '-new-signature'
        second = self.fetch_feed()
        first_url = ElementTree.fromstring(first.data).find('channel/item/enclosure').get('url')
        second_url = ElementTree.fromstring(second.data).find('channel/item/enclosure').get('url')
        self.assertEqual(first_url, second_url)

    def test_playback_checks_current_account_access(self):
        response = self.fetch_feed()
        enclosure_url = ElementTree.fromstring(response.data).find(
            'channel/item/enclosure').get('url')
        self.audio = []
        playback = self.client.get(enclosure_url)
        self.assertEqual(playback.status_code, 404)
        self.assertNotIn('Location', playback.headers)

    def test_playback_denied_missing_or_expired_access(self):
        for status, expected_status in ((403, 404), (404, 404), (401, 401), (500, 502)):
            with self.subTest(status=status):
                self.denied_status = status
                response = self.client.get('/longplay/audio/102.mp3', query_string={
                    'username': 'reader@example.com', 'password': 'secret&+password',
                })
                self.assertEqual(response.status_code, expected_status)
                self.assertNotIn('Location', response.headers)

    def test_playback_head_resolves_without_downloading_mp3(self):
        response = self.client.head('/longplay/audio/100.mp3', query_string={
            'username': 'reader@example.com', 'password': 'secret&+password',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], self.audio[0]['url'])
        self.assertEqual(response.data, b'')

    def test_playback_requires_credentials(self):
        response = self.client.get('/longplay/audio/100.mp3')
        self.assertEqual(response.status_code, 400)
        self.session.post.assert_not_called()

    def test_playback_timeout(self):
        self.session.get.side_effect = requests.Timeout()
        response = self.client.get('/longplay/audio/100.mp3', query_string={
            'username': 'reader@example.com', 'password': 'secret&+password',
        })
        self.assertEqual(response.status_code, 504)


if __name__ == '__main__':
    unittest.main()
