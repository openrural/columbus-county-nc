
import json
import urllib
import urllib2
import logging
import traceback

import ebdata.retrieval.log  # sets up base handlers.
from ebdata.retrieval.scrapers.newsitem_list_detail import NewsItemListDetailScraper
from ebpub.geocoder import GeocodingException, ParsingError, AmbiguousResult
from ebpub.streets.models import ImproperCity

from openrural.error_log.models import Error as ScraperError

logging.getLogger().setLevel(logging.DEBUG)

class ScraperWikiScraper(NewsItemListDetailScraper):

    url = "http://api.scraperwiki.com/api/1.0/datastore/sqlite"
    list_filter = None
    ordering = None
    limit = 50

    def __init__(self, *args, **kwargs):
        clear = kwargs.pop('clear', False)
        super(ScraperWikiScraper, self).__init__(*args, **kwargs)
        if clear:
            self._create_schema()
        self.num_added = 0
        self.num_total = 0
        self.num_geocode = 0
        self.num_geocode_success = 0

    def get_query(self, select='*', limit=10, offset=0):
        where = ''
        if self.list_filter:
            parts = []
            for key, val in self.list_filter.iteritems():
                parts.append("{0} = '{1}'".format(key, val))
            where = ' AND '.join(parts)
        query = ['SELECT {0} FROM `swdata`'.format(select)]
        if where:
            query.append('WHERE {0}'.format(where))
        if self.ordering:
            query.append('ORDER BY {0}'.format(self.ordering))
        if limit > 0:
            query.append('LIMIT {0}'.format(limit))
        if offset > 0:
            query.append('OFFSET {0}'.format(offset))
        query = ' '.join(query)
        self.logger.debug(query)
        return query

    def get_url(self, query):
        args = {'name': self.scraper_name, "format": "jsondict",
                "query": query}
        url = "{0}?{1}".format(self.url, urllib.urlencode(args))
        self.logger.info(url)
        return self.get_html(url)

    def count(self):
        query = self.get_query(select='COUNT(*) AS count', limit=0, offset=0)
        data = json.loads(self.get_url(query=query))[0]
        return data['count']

    def list_pages(self):
        count = self.count()
        offset = 0
        while offset < count:
            yield self.get_url(query=self.get_query(limit=self.limit, offset=offset))
            offset += self.limit

    def parse_list(self, data):
        for row in json.loads(data):
            yield row

    def update(self):
        super(ScraperWikiScraper, self).update()
        geocode_rate = float(self.num_geocode_success) / self.num_geocode
        self.logger.info('Geocode success rate {:.2%}'.format(geocode_rate))

    def geocode(self, location_name, zipcode=None):
        """
        Tries to geocode the given location string, returning a Point object
        or None.
        """
        self.num_geocode += 1
        # Try to lookup the adress, if it is ambiguous, attempt to use
        # any provided zipcode information to resolve the ambiguity.
        # The zipcode is not included in the initial pass because it
        # is often too picky yeilding no results when there is a
        # legitimate nearby zipcode identified in either the address
        # or street number data.
        try:
            loc = self._geocoder.geocode(location_name)
            self.num_geocode_success += 1
            return loc
        except AmbiguousResult as result:
            # try to resolve based on zipcode...
            if zipcode is None:
                self.logger.info(
                    "Ambiguous results for address %s. (no zipcode to resolve dispute)" %
                    (location_name, ))
                return None
            in_zip = [r for r in result.choices if r['zip'] == zipcode]
            if len(in_zip) == 0:
                self.logger.info(
                    "Ambiguous results for address %s, but none in specified zipcode %s" %
                    (location_name, zipcode))
                return None
            elif len(in_zip) > 1:
                self.logger.info(
                    "Ambiguous results for address %s in zipcode %s, guessing first." %
                    (location_name, zipcode))
                return in_zip[0]
            else:
                return in_zip[0]
        except (GeocodingException, ParsingError, ImproperCity) as e:
            ScraperError.objects.create(
                scraper=self.schema_slugs[0],
                name=type(e).__name__,
                location=location_name,
                zipcode=zipcode or '',
                description=traceback.format_exc(),
            )
            self.logger.error(unicode(e))
            return None
