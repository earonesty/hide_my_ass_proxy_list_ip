import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scrapy.http import HtmlResponse
from scrapy.crawler import CrawlerProcess

from scraper import HideMyAssSpider, SWPipeline


def row(address, classes='altshade'):
    return ('<table><tr class="%s"><td></td><td><span>%s</span></td>'
            '<td>8080</td><td><span> US </span></td><td></td><td></td>'
            '<td>HTTP</td><td>High</td></tr></table>') % (classes, address)


def response(body):
    return HtmlResponse('http://hidemyass.com/proxy-list/', body=body.encode(), encoding='utf-8')


class ParsingTests(unittest.TestCase):
    def test_old_and_new_pagination(self):
        for markup in (
            '<div id="container"><div id="pagination"><ul><div><li class="nextpageactive"><a href="/2">Next</a></li></div></ul></div></div>',
            '<div id="pagination"><li class="enabled nextpageactive extra"><a href="/2">Next</a></li></div>',
            '<a class="next" href="/2">Next</a>',
            '<a class="enabled\t next\n extra" href="/2">Next</a>',
        ):
            with self.subTest(markup=markup):
                links = HideMyAssSpider.rules[0].link_extractor.extract_links(response(markup))
                self.assertEqual([link.url for link in links], ['http://hidemyass.com/2'])

    def test_non_next_links_and_deduplication(self):
        markup = ('<a class="nextish" href="/bad">No</a><a class="previous" href="/bad2">No</a>'
                  '<div id="pagination"><li class="nextpageactive"><a class="next" href="/2">Next</a></li></div>')
        links = HideMyAssSpider.rules[0].link_extractor.extract_links(response(markup))
        self.assertEqual([link.url for link in links], ['http://hidemyass.com/2'])

    def test_ip_obfuscation_old_and_spaced_css(self):
        for style in ('.hide{display:none}', '.hide, .other { DISPLAY : none !important; }'):
            markup = row('<style>%s</style>1.<span class="extra hide">99.</span>'
                         '<span>2.</span><div style="color:red; DISPLAY : none !important">88.</div>'
                         '<span style="display:inline">3.</span>4' % style, 'extra altshade zebra')
            item = list(HideMyAssSpider().parse_proxy_page(response(markup)))[0]
            self.assertEqual(dict(item), {'ipaddress': '1.2.3.4', 'port': '8080', 'country': 'US',
                                         'proxy_type': 'HTTP', 'anonimity': 'High',
                                         'url': 'http://hidemyass.com/proxy-list/'})

    def test_ip_without_style_block(self):
        item = list(HideMyAssSpider().parse_start_url(response(row('1.2.3.4'))))[0]
        self.assertEqual(item['ipaddress'], '1.2.3.4')

    def test_sqlite_buffer_and_close_flush(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            try:
                os.chdir(directory)
                crawler = CrawlerProcess({'SW_SAVE_BUFFER': 2, 'LOG_ENABLED': False}).create_crawler(HideMyAssSpider)
                pipeline = SWPipeline.from_crawler(crawler)
                spider = HideMyAssSpider()
                for ip in ['1.2.3.4', '2.3.4.5']:
                    pipeline.process_item({'ipaddress': ip, 'port': '8080'}, spider)
                self.assertEqual(pipeline.data, [])
                pipeline.process_item({'ipaddress': '3.4.5.6', 'port': '80'}, spider)
                pipeline.spider_closed(spider)
                import scraperwiki
                records = scraperwiki.sqlite.select('* from hidemyass order by ipaddress')
                self.assertEqual([x['ipaddress'] for x in records], ['1.2.3.4', '2.3.4.5', '3.4.5.6'])
            finally:
                os.chdir(previous)

    def test_real_crawl_pagination_and_json_feed(self):
        pages = {
            '/1': row('1.2.3.4') + '<div id="pagination"><li class="extra nextpageactive"><a href="/2">Next</a></li></div>',
            '/2': row('2.3.4.5') + '<a class="extra next" href="/3">Next</a>',
            '/3': row('3.4.5.6'),
        }
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(pages[self.path].encode())
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'proxylist.json'
                script = """import sys
from scraper import HideMyAssSpider, run_spider
class FixtureSpider(HideMyAssSpider):
    start_urls = [sys.argv[1]]
    allowed_domains = ['127.0.0.1']
run_spider(FixtureSpider, {'FEEDS': {sys.argv[2]: {'format': 'jsonlines'}},
                         'LOG_ENABLED': False, 'TELNETCONSOLE_ENABLED': False})
"""
                subprocess.run([sys.executable, '-c', script,
                                'http://127.0.0.1:%s/1' % server.server_port, str(output)],
                               check=True, timeout=30)
                items = [json.loads(line) for line in output.read_text().splitlines()]
                self.assertEqual([x['ipaddress'] for x in items], ['1.2.3.4', '2.3.4.5', '3.4.5.6'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
