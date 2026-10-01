#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import re

from scrapy import signals
from scrapy.crawler import CrawlerProcess
from scrapy.spiders import CrawlSpider, Rule
from scrapy.linkextractors import LinkExtractor
from scrapy.item import Item, Field
from scrapy.loader import ItemLoader
from itemloaders.processors import MapCompose, TakeFirst


def class_xpath(name):
    """Match a complete HTML class token, regardless of order or whitespace."""
    return "contains(concat(' ', normalize-space(@class), ' '), ' %s ')" % name


NEXT_PAGE_XPATHS = (
    '//a[%s]' % class_xpath('next'),
    '//div[@id="pagination"]//li[%s]/a' % class_xpath('nextpageactive'),
)

# Old pages use compact declarations; newer pages may add spaces or !important.
HIDDEN_STYLE = re.compile(r'(?:^|;)\s*display\s*:\s*none\s*(?:!important\s*)?(?:;|$)', re.I)
CSS_RULE = re.compile(r'([^{}]+)\{([^{}]*)\}')
CSS_CLASS = re.compile(r'\.([A-Za-z_][\w-]*)')


def visible_text(part, hidden_classes):
    """Collect descendant text while omitting hidden elements and their children."""
    if isinstance(part.root, str):
        return part.get()
    if not isinstance(part.root.tag, str) or part.root.tag.lower() in {'style', 'script'}:
        return ''
    if HIDDEN_STYLE.search(part.attrib.get('style', '')):
        return ''
    if hidden_classes.intersection(part.attrib.get('class', '').split()):
        return ''
    return ''.join(visible_text(child, hidden_classes) for child in part.xpath('node()'))


class HideMyAssSpider(CrawlSpider):
    name = 'hidemyass'
    start_urls = ['http://hidemyass.com/proxy-list/']
    allowed_domains = ['hidemyass.com']

    rules = (
        Rule(LinkExtractor(restrict_xpaths=NEXT_PAGE_XPATHS),
             callback='parse_proxy_page', follow=True),
    )

    def parse_start_url(self, response, **kwargs):
        yield from self.parse_proxy_page(response)

    def parse_proxy_page(self, response):
        links = response.xpath('//tr[%s]' % class_xpath('altshade'))

        for link in links:
            ipaddress_parts = link.xpath('td[2]/span')
            hidden_classes = set()
            for style in ipaddress_parts.xpath('style/text()').getall():
                for selectors, declarations in CSS_RULE.findall(style):
                    if HIDDEN_STYLE.search(declarations):
                        # Restrict this to class-only selectors used by the IP
                        # obfuscation, rather than interpreting arbitrary CSS.
                        for selector in selectors.split(','):
                            selector = selector.strip()
                            if re.fullmatch(r'\.[A-Za-z_][\w-]*', selector):
                                hidden_classes.update(CSS_CLASS.findall(selector))

            ipaddress = []
            for part in ipaddress_parts.xpath('span|div|text()'):
                text = visible_text(part, hidden_classes)
                for octet in text.split('.'):
                    octet = octet.strip()
                    if octet.isdigit():
                        ipaddress.append(octet)

            ipaddress = '.'.join(ipaddress)

            loader = WebsiteLoader(selector=link)
            loader.add_value('ipaddress', ipaddress)
            loader.add_xpath('port', 'td[3]/text()')
            loader.add_xpath('country', 'td[4]/span/text()')
            loader.add_xpath('proxy_type', 'td[7]/text()')
            loader.add_xpath('anonimity', 'td[8]/text()')
            loader.add_value('url', response.url)

            item = loader.load_item()

            yield item


class Website(Item):
    url = Field()
    ipaddress = Field()
    port = Field()
    country = Field()
    speed = Field()
    connection_time = Field()
    proxy_type = Field()
    anonimity = Field()


class WebsiteLoader(ItemLoader):
    default_item_class = Website
    default_input_processor = MapCompose(lambda x: x.strip())
    default_output_processor = TakeFirst()


class SWPipeline(object):
    """A pipeline for saving to the Scraperwiki datastore"""
    def __init__(self):
        self.buffer = 20
        self.data = []

    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls()
        pipeline.buffer = crawler.settings.getint('SW_SAVE_BUFFER', 20)
        crawler.signals.connect(pipeline.spider_closed, signals.spider_closed)
        return pipeline

    def process_item(self, item, spider):
        self.data.append(dict(item))
        if len(self.data) >= self.buffer:
            self.write_data(spider)
        return item

    def spider_closed(self, spider):
        if self.data:
            self.write_data(spider)

    def write_data(self, spider):
        unique_keys = ['ipaddress']
        import scraperwiki
        scraperwiki.sqlite.save(table_name=spider.name, unique_keys=unique_keys, data=self.data)
        self.data = []


def run_spider(spider, settings):
    """Run a spider with given settings"""
    crawler = CrawlerProcess(settings)
    crawler.crawl(spider)
    crawler.start()


def main():
    options = {
        'LOG_LEVEL': 'DEBUG',
        'FEEDS': {'proxylist.json': {'format': 'jsonlines', 'overwrite': True}},
    }

    run_spider(HideMyAssSpider, options)


def scraper():
    options = {
        'SW_SAVE_BUFFER': 30,
        'ITEM_PIPELINES': {SWPipeline: 300},
    }

    run_spider(HideMyAssSpider, options)


if __name__ == '__main__':
    main()
