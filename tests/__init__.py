#!/usr/bin/env python3

import asyncio
import contextlib
import logging
import math
import socket
import unittest
import unittest.mock
import urllib.parse
import warnings

import PIL.Image
import requests
import web_cache

import sacad


web_cache.DISABLE_PERSISTENT_CACHING = True


def is_internet_reachable():
  try:
    # open TCP socket to Google DNS server
    with socket.create_connection(("8.8.8.8", 53)):
      pass
  except OSError as e:
    if e.errno == 101:
      return False
    raise
  return True


def download(url, filepath=None):
  with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=requests.packages.urllib3.exceptions.InsecureRequestWarning)

    with contextlib.closing(requests.get(url, timeout=5, verify=False, stream=(filepath is not None))) as response:
      response.raise_for_status()
      if filepath is None:
        return response.content
      with open(filepath, "wb") as f:
        for chunk in response.iter_content(2 ** 14):
          f.write(chunk)


def sched_and_run(coroutine, async_loop, delay=0):
  async def delay_coroutine(coroutine, delay):
    r = await coroutine
    if delay > 0:
      # time to cleanup aiohttp objects
      # see https://aiohttp.readthedocs.io/en/stable/client_advanced.html#graceful-shutdown
      await asyncio.sleep(delay)
    return r
  future = asyncio.ensure_future(delay_coroutine(coroutine, delay),
                                 loop=async_loop)
  async_loop.run_until_complete(future)
  return future.result()


@unittest.skipUnless(is_internet_reachable(), "Need Internet access")
class TestSacad(unittest.TestCase):

  @staticmethod
  def getImgInfo(img_filepath):
    with open(img_filepath, "rb") as img_file:
      img = PIL.Image.open(img_file)
      format = img.format.lower()
      format = sacad.SUPPORTED_IMG_FORMATS[format]
      width, height = img.size
    return format, width, height

  def test_getMasterOfPuppetsCover(self):
    """ Search and download cover for 'Master of Puppets' with different parameters. """
    async_loop = asyncio.get_event_loop()
    for format in sacad.cover.CoverImageFormat:
      for size in (300, 600, 1200):
        for size_tolerance in (0, 25, 50):
          with self.subTest(format=format, size=size, size_tolerance=size_tolerance):
            with sacad.mkstemp_ctx.mkstemp(prefix="sacad_test_",
                                           suffix=".%s" % (format.name.lower())) as tmp_filepath:
              coroutine = sacad.search_and_download("Master of Puppets",
                                                    "Metallica",
                                                    format,
                                                    size,
                                                    tmp_filepath,
                                                    size_tolerance_prct=size_tolerance,
                                                    amazon_tlds=(),
                                                    no_lq_sources=False,
                                                    async_loop=async_loop)
              sched_and_run(coroutine, async_loop, delay=0.5)
              out_format, out_width, out_height = __class__.getImgInfo(tmp_filepath)
              self.assertEqual(out_format, format)
              self.assertLessEqual(out_width, size * (100 + size_tolerance) / 100)
              self.assertGreaterEqual(out_width, size * (100 - size_tolerance) / 100)
              self.assertLessEqual(out_height, size * (100 + size_tolerance) / 100)
              self.assertGreaterEqual(out_height, size * (100 - size_tolerance) / 100)

  def test_getImageUrlMetadata(self):
    """ Download the beginning of image files to guess their format and resolution. """
    async_loop = asyncio.get_event_loop()
    refs = {"https://www.nuclearblast.de/static/articles/152/152118.jpg/1000x1000.jpg": (sacad.cover.CoverImageFormat.JPEG,
                                                                                         (700, 700),
                                                                                         math.ceil(18000 / sacad.CoverSourceResult.METADATA_PEEK_SIZE_INCREMENT)),
            "http://img2-ak.lst.fm/i/u/55ad95c53e6043e3b150ba8a0a3b20a1.png": (sacad.cover.CoverImageFormat.PNG,
                                                                               (600, 600),
                                                                               1)}
    for url, (ref_fmt, ref_size, block_read) in refs.items():
      sacad.CoverSourceResult.guessImageMetadataFromData = unittest.mock.Mock(wraps=sacad.CoverSourceResult.guessImageMetadataFromData)
      source = unittest.mock.Mock()
      source.http = sacad.http_helpers.Http(allow_session_cookies=False,
                                            min_delay_between_accesses=1 / 3,
                                            logger=logging.getLogger())
      cover = sacad.CoverSourceResult(url,
                                      None,
                                      None,
                                      source=source,
                                      thumbnail_url=None,
                                      source_quality=sacad.cover.CoverSourceQuality.NORMAL,
                                      check_metadata=sacad.cover.CoverImageMetadata.ALL)
      coroutine = cover.updateImageMetadata()
      sched_and_run(coroutine, async_loop, delay=0.5)
      self.assertEqual(cover.size, ref_size)
      self.assertEqual(cover.format, ref_fmt)
      self.assertGreaterEqual(sacad.CoverSourceResult.guessImageMetadataFromData.call_count, 0)
      self.assertLessEqual(sacad.CoverSourceResult.guessImageMetadataFromData.call_count, block_read)

  def test_compareImageSignatures(self):
    """ Compare images using their signatures. """
    urls = ("http://wac.450f.edgecastcdn.net/80450F/kool1017.com/files/2013/09/cover_highway_to_hell_500x500.jpg",
            "http://www.jesus-is-savior.com/Evils%20in%20America/Rock-n-Roll/highway_to_hell-large.jpg",
            "http://i158.photobucket.com/albums/t113/gatershanks/Glee%20Alternative%20Song%20Covers/1x14%20Hell%20O/1x14Hell-O-HighwayToHell.jpg")
    img_sig = {}
    for i, url in enumerate(urls):
      img_data = download(url)
      img_sig[i] = sacad.CoverSourceResult.computeImgSignature(img_data)
    self.assertTrue(sacad.CoverSourceResult.areImageSigsSimilar(img_sig[0], img_sig[1]))
    self.assertTrue(sacad.CoverSourceResult.areImageSigsSimilar(img_sig[1], img_sig[0]))
    self.assertFalse(sacad.CoverSourceResult.areImageSigsSimilar(img_sig[0], img_sig[2]))
    self.assertFalse(sacad.CoverSourceResult.areImageSigsSimilar(img_sig[1], img_sig[2]))
    self.assertFalse(sacad.CoverSourceResult.areImageSigsSimilar(img_sig[2], img_sig[0]))
    self.assertFalse(sacad.CoverSourceResult.areImageSigsSimilar(img_sig[2], img_sig[1]))

    urls = ("https://images-na.ssl-images-amazon.com/images/I/91euo%2BzpKEL._SL1500_.jpg",
            "https://lastfm-img2.akamaized.net/i/u/300x300/c971ea7edb14ede6bab2f94666bb9005.png")
    img_sig = {}
    for i, url in enumerate(urls):
      img_data = download(url)
      img_sig[i] = sacad.CoverSourceResult.computeImgSignature(img_data)
    self.assertFalse(sacad.CoverSourceResult.areImageSigsSimilar(img_sig[0], img_sig[1]))

  def test_coverSources(self):
    """ Check all sources return valid results with different parameters. """
    async_loop = asyncio.get_event_loop()
    for size in range(300, 1200 + 1, 300):
      source_args = (size, 0)
      sources = [sacad.sources.LastFmCoverSource(*source_args),
                 sacad.sources.GoogleImagesWebScrapeCoverSource(*source_args),
                 sacad.sources.AmazonDigitalCoverSource(*source_args)]
      sources.extend(sacad.sources.AmazonCdCoverSource(*source_args, tld=tld) for tld in sacad.sources.AmazonCdCoverSource.TLDS)
      for source in sources:
        for artist, album in zip(("Michael Jackson", "Björk"), ("Thriller", "Vespertine")):
          with self.subTest(size=size, source=source, artist=artist, album=album):
            coroutine = source.search(album, artist)
            results = sched_and_run(coroutine, async_loop, delay=0.5)
            coroutine = sacad.CoverSourceResult.preProcessForComparison(results, size, 0)
            results = sched_and_run(coroutine, async_loop, delay=0.5)
            if not (((size > 500) and isinstance(source, sacad.sources.AmazonCdCoverSource)) or
                    ((size > 500) and isinstance(source, sacad.sources.LastFmCoverSource)) or
                    (isinstance(source, sacad.sources.AmazonCdCoverSource) and (artist == "Björk") and
                     (urllib.parse.urlsplit(source.base_url).netloc.rsplit(".", 1)[-1] == "cn"))):
              self.assertGreaterEqual(len(results), 1)

            for result in results:
              self.assertTrue(result.urls)
              self.assertIn(result.format, sacad.cover.CoverImageFormat)
              self.assertGreaterEqual(result.size[0], size)

    # test for specific cover not available on amazon.com, but on amazon.de
    size = 290
    source = sacad.sources.AmazonCdCoverSource(size, 0, tld="de")
    coroutine = source.search("Dream Dance 5", "Various")
    results = sched_and_run(coroutine, async_loop, delay=0.5)
    self.assertGreaterEqual(len(results), 1)
    for result in results:
      self.assertTrue(result.urls)
      self.assertIn(result.format, sacad.cover.CoverImageFormat)
      self.assertGreaterEqual(result.size[0], size)

    # check last.fm handling of queries with punctuation
    for artist, album in zip(("Megadeth", "Royal City"),
                             ("So Far, So Good, So What?", "Little Heart's Ease")):
      size = 300
      source = sacad.sources.LastFmCoverSource(size, 0)
      coroutine = source.search(album, artist)
      results = sched_and_run(coroutine, async_loop, delay=0.5)
      self.assertGreaterEqual(len(results), 1)


  def test_unaccentuate(self):
    self.assertEqual(sacad.sources.base.CoverSource.unaccentuate("EéeAàaOöoIïi"), "EeeAaaOooIii")

  def test_is_square(self):
    for x in range(1, 100):
      if x in (1, 4, 9, 16, 25, 36, 49, 64, 81):
        self.assertTrue(sacad.cover.is_square(x), x)
      else:
        self.assertFalse(sacad.cover.is_square(x), x)


# logging
#logging.basicConfig(level=logging.DEBUG)
logging.basicConfig(level=logging.CRITICAL + 1)


if __name__ == "__main__":
  # run tests
  unittest.main()
