# coding: utf-8

from flask import make_response
from app import app
import os
import datetime
import requests
from rfeed import *

@app.route('/rss', methods=['GET'])
def get_rss():
  url = 'https://api.uusijuttu.fi/api/v2/discover/items'
  token = os.environ['TOKEN']
  response = requests.get(url, headers={"Authorization": "Bearer " + token})
  rss_items = []
  for items in response.json()['items']:
    rss_items.append(Item(
      title = items['story']['title'],
      link = items['story']['story_content']['meta']['audioFiles'][0],
      pubDate = datetime.datetime.fromisoformat(items['story']['published_at'])))

  feed = Feed(
    title = 'Uusijuttu.fi RSS',
    link = 'http://example.com',
    description = 'Unofficial feed',
    items = rss_items)

  response = make_response(feed.rss(), 200)
  response.mimetype = "text/plain"
  return response
