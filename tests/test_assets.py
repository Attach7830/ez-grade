"""The actual HTML entry point must resolve every script and stylesheet."""
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('src'):
            self.assets.append((attrs['src'],'javascript'))
        if tag == 'link' and attrs.get('rel') == 'stylesheet':
            self.assets.append((attrs['href'],'css'))


class BrowserEntryTests(unittest.TestCase):
    def test_entry_point_assets_are_local_and_served(self):
        with TemporaryDirectory() as directory:
            with TestClient(create_app(Settings(Path(directory),'synthetic-password','x'*64))) as client:
                entry = client.get('/')
                self.assertEqual(entry.status_code,200)
                parser = AssetParser()
                parser.feed(entry.text)
                self.assertEqual(len(parser.assets),2)
                for url,mime in parser.assets:
                    self.assertTrue(url.startswith('/static/'))
                    asset = client.get(url)
                    self.assertEqual(asset.status_code,200,url)
                    self.assertIn(mime,asset.headers['content-type'])
                    self.assertGreater(len(asset.content),100)
                self.assertIn("connect-src 'self'",entry.headers['content-security-policy'])


if __name__ == '__main__':
    unittest.main()
