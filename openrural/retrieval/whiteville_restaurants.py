#!/usr/bin/env python

import sys
import csv
import datetime
from optparse import OptionParser
from collections import defaultdict

from django.template import defaultfilters as filters
from django.core.serializers.json import DjangoJSONEncoder

from ebpub.db.models import Schema, SchemaField
from ebpub.utils.script_utils import add_verbosity_options
from ebdata.retrieval.scrapers.newsitem_list_detail import NewsItemListDetailScraper

from openrural.data_dashboard.scrapers import DashboardMixin


FACILITY_STATUS_CODES = {
    'A': 'Establishment open for business.',
    'B': 'Permit or license valid but establishment not open for business at a particular time of year (seasonal).',
    'C': 'Permit or license valid but establishment not open for business for unknown reasons (bankruptcy, etc.)',
    'D': 'Permit suspended and establishment closed for nonpayment of fees.',
    'E': 'Permit suspended and establishment closed for rule or law violations.',
    'F': 'Transitional permit expired and establishment closed due to noncompliance of conditions on transitional permit.',
    'G': 'Permit revoked and establishment closed. (Building destroyed etc.)',
    'H': 'Permit invalid due to sale of business or establishment has been upgraded from food stand to restaurant or if the license or permit issued from another agency has become invalid.',
    'I': 'New permit (not transitional permit - see T status) issued and establishment opened for business or a license from another agency has been issued.',
    'J': 'Permit expired because establishment has not been in operation for one year in accordance with GS 130A-248(b1).',
    'K': 'Transitional permit becomes a permanent permit.',
    'L': 'To be used with Child Care Centers only. Used when a childcare center changes ownership and earns an Approved, Provisional or Disapproved classification. Used to show facility has a pending status.',
    'M': 'Mail returned. Inspection still due. A request for a correct address has been sent to the county.',
    'S': 'Services for the Blind exempt from fee. Inspections still due.',
    'T': 'Transitional Permit issued and establishment open for business.',
    'U': 'The facility received an inspection while under a transitional permit.',
    'W': 'The facility has received an Intent to Suspend for rule violation that is not associated with billing. (Inspection required.)',
    'X': 'Administrative stop clock for billing. Inspections still due (Example: late Letter).',
    'Z': 'Administrative error. No inspection required. Account should never have been assigned this ID number.'
}


class RestaurantInspections(DashboardMixin, NewsItemListDetailScraper):

    logname = 'restaurant-scraper'
    schema_slugs = ('restaurant-inspections',)

    def update(self, csvreader):
        insp_dict = defaultdict(list)
        for item in csvreader:
            key = '%s %s' % (item['FacilityID'], item['ACTIVITY_DATE'])
            insp_dict[key].append(item)

        for key, val in insp_dict.items():
            self.parse_insp_list(val)

    def parse_insp_list(self, rows):
        row = rows[0]
        status = row['STATUS_CODE']
        attrs = {
            'restaurant_id': row['FacilityID'],
            'name': row['FAC_NAME'],
            'status_code': self.get_or_create_lookup('status_code', status, status, description=FACILITY_STATUS_CODES[status]).id,
            'score': int(float(row['ACTIVITY_FINAL_SCORE'])*100),
        }

        form_item_lookups = [self.get_or_create_lookup('form_item',
            r['FORM_ITEM_ID'], r['FORM_ITEM_ID'], description=r['FORM_ITEM_DESC']) for r in rows]
        form_item_text = ','.join(str(v.id) for v in form_item_lookups)
        if len(form_item_text) > 4096:
            # This is an ugly hack to work around the fact that
            # many-to-many Lookups are themselves an ugly hack.
            # See http://developer.openblockproject.org/ticket/143
            form_item_text = form_item_text[0:4096]
            form_item_text = form_item_text[0:form_item_text.rindex(',')]
            self.logger.warning('Restaurant %r had too many violations to store, skipping some!', attrs['name'])

        # There's a bunch of data about every particular violation, and we
        # store it as a JSON object. Here, we create the JSON object.
        i_lookup_dict = dict([(v.code, v) for v in form_item_lookups])
        i_list = [{'desc': i_lookup_dict[r['FORM_ITEM_ID']].description, 'comment': r['ACTIVITY_ITEM_COMMENT']} for r in rows]
        i_json = DjangoJSONEncoder().encode(i_list)

        title = filters.title(attrs['name'])
        item_date = datetime.datetime.strptime(row['ACTIVITY_DATE'], "%m/%d/%Y")
        attrs.update({
            'form_item': form_item_text,
            'comments': i_json,
        })
        self.create_newsitem(
            attrs,
            title=title,
            item_date=item_date,
            location_name=filters.title(row['ADDR_LINE1']),
            city=row['ADDR_CITY'],
            state=row['STATE_CODE'],
            zipcode=row['ADDR_ZIP5'],
        )

    def _create_schema(self):
        try:
            Schema.objects.get(slug=self.schema_slugs[0]).delete()
        except Schema.DoesNotExist:
            pass
        schema = Schema.objects.create(
            name='Restaurant Inspection',
            plural_name='Restaurant Inspections',
            indefinite_article = 'a',
            slug=self.schema_slugs[0],
            last_updated=datetime.datetime.now(),
            is_public=True,
            has_newsitem_detail=True,
        )
        SchemaField.objects.create(
            schema=schema,
            pretty_name="Restaurant Name",
            pretty_name_plural="Restaurant Names",
            real_name='varchar01',
            name='name',
        )
        SchemaField.objects.create(
            schema=schema,
            pretty_name="Restaurant ID",
            pretty_name_plural="Restaurant IDs",
            real_name='int01',
            name='resaurant_id',
        )
        SchemaField.objects.create(
            schema=schema,
            pretty_name="Score",
            pretty_name_plural="Scores",
            real_name='int02',
            name='score',
        )
        SchemaField.objects.create(
            schema=schema,
            is_lookup=True,
            pretty_name="Form Items",
            pretty_name_plural="Form Items",
            real_name='varchar02',
            name='form_item',
        )
        SchemaField.objects.create(
            schema=schema,
            pretty_name="Comments",
            pretty_name_plural="Comments",
            real_name='text01',
            name='comments',
        )
        SchemaField.objects.create(
            schema=schema,
            is_lookup=True,
            pretty_name="Status Code",
            pretty_name_plural="Status Codes",
            real_name='int03',
            name='status_code',
        )


def main():
    parser = OptionParser()
    parser.add_option('-c', '--clear', help='Clear schema',
                      action="store_true", dest="clear")
    add_verbosity_options(parser)
    opts, args = parser.parse_args(sys.argv)
    if len(args) != 2:
        parser.error("Please specify a CSV file to import")
    csvreader = csv.DictReader(open(args[1]))
    RestaurantInspections(clear=opts.clear).run(csvreader)


if __name__ == '__main__':
    sys.exit(main())
