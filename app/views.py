# coding: utf-8

from flask import make_response, request
from app import app
import os
import datetime
import requests
from rfeed import *

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
    rss_items.append(Item(
      title = items['story'].get('series_id', items['story']['story_content']['meta']['type']) + ': ' + items['story']['title'],
      enclosure = Enclosure(url=items['story']['story_content']['meta']['audioFiles'][0], length=items['story']['audio_length'], type='audio/mpeg'),
      pubDate = datetime.datetime.fromisoformat(items['story']['published_at']),
      description = items['story']['story_content']['content'].get('audio_description', ''),
      author = items['story']['author']['author_content']['firstname'] + ' ' + items['story']['author']['author_content']['lastname']
    ))

  feed = Feed(
    title = 'Uusijuttu.fi RSS',
    link = 'http://example.com',
    description = 'Unofficial feed',
    items = rss_items)

  response = make_response(feed.rss(), 200)
  response.mimetype = "text/plain"
  return response
