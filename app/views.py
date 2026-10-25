# coding: utf-8

from flask import make_response, redirect, request, url_for
from app import app
import os
import datetime
import requests
from rfeed import *
from app.longplay import (
  LongplayAuthenticationError, LongplayAudioUnavailableError,
  build_longplay_feed, resolve_longplay_audio,
)
from xml.etree import ElementTree

@app.route('/rss', methods=['GET'])
def get_rss():
  url = 'https://db.uusijuttu.fi/auth/v1/token?grant_type=password'
  json = {
    'email': request.args.get('username'),
    'password': request.args.get('password'),
    'gotrue_meta_security': {}
  }
  response = requests.post(url, json=json, headers={"apikey": os.environ['APIKEY'], "Content-Type": "application/json;charset=UTF-8"})
  token = response.json()['access_token']

  url = 'https://api.uusijuttu.fi/api/v2/discover/items?include_helicopter=1'
  response = requests.get(url, headers={"Authorization": "Bearer " + token})
  rss_items = []
  for items in response.json()['items']:
    if 'story' not in items or items['story'] is None:
      continue
    author = items['story']['author']['author_content']['firstname'] + ' ' + items['story']['author']['author_content']['lastname']
    itunes_item = iTunesItem(
      author = author,
      image = 'https://uusijuttu.imgix.net/' + items['story']['social_image']['image']['url'] if (items['story']['social_image'] and items['story']['social_image']['image']) else '',
      duration = items['story']['audio_length'],
      subtitle = items['story']['subhead'],
      summary = items['story']['story_content']['content'].get('audio_description', '')
    )
    rss_items.append(Item(
      title = (items['story']['series_id'] or items['story']['story_content']['meta']['type']) + ': ' + items['story']['title'],
      enclosure = Enclosure(url=items['story']['story_content']['meta']['audioFiles'][0], length=items['story']['audio_length'], type='audio/mpeg'),
      pubDate = datetime.datetime.fromisoformat(items['story']['published_at']),
      description = items['story']['story_content']['content'].get('audio_description', items['story']['story_content']['content'].get('socialDescription', '')),
      author = author,
      extensions = [itunes_item]
    ))

  feed = Feed(
    title = 'Uusijuttu.fi RSS',
    link = 'http://example.com',
    description = 'Unofficial feed',
    items = rss_items)

  response = make_response(feed.rss(), 200)
  response.mimetype = "text/plain"
  return response

@app.route('/longplay', methods=['GET'])
def get_longplay():
  def fetch(username, password):
    feed = build_longplay_feed(username, password, lambda node_id: url_for(
      'get_longplay_audio', node_id=node_id, username=username, password=password,
      _external=True,
    ))
    response = make_response(feed, 200)
    response.mimetype = 'application/rss+xml'
    return response

  return _longplay_response(fetch)


@app.route('/longplay/audio/<int:node_id>.mp3', methods=['GET', 'HEAD'])
def get_longplay_audio(node_id):
  return _longplay_response(lambda username, password: redirect(
    resolve_longplay_audio(username, password, node_id), code=302,
  ))


def _longplay_response(fetch):
  username = request.args.get('username')
  password = request.args.get('password')
  if not username or not password:
    response = make_response('Longplay username and password are required.', 400)
  else:
    try:
      response = fetch(username, password)
    except LongplayAuthenticationError as error:
      app.logger.warning('Longplay authentication failed: stage=%s upstream_status=%s oauth_error=%s',
                         error.stage, error.status, error.oauth_error or 'unknown')
      response = make_response(str(error) + '\n', 401)
    except LongplayAudioUnavailableError:
      response = make_response('Longplay audio is unavailable for this account.', 404)
    except requests.Timeout:
      response = make_response('Longplay request timed out.', 504)
    except (requests.RequestException, ValueError, KeyError, TypeError, ElementTree.ParseError):
      response = make_response('Unable to fetch the Longplay feed.', 502)

  response.headers['Cache-Control'] = 'private, no-store'
  response.headers['Referrer-Policy'] = 'no-referrer'
  if response.status_code >= 400:
    response.mimetype = 'text/plain'
  return response
