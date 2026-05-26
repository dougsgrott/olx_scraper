import scrapy


class CatalogItem(scrapy.Item):
    uid = scrapy.Field()
    title = scrapy.Field()
    details = scrapy.Field()
    badges = scrapy.Field()
    pricing = scrapy.Field()
    date = scrapy.Field()
    location = scrapy.Field()
    code = scrapy.Field()
    scraped_date = scrapy.Field()
    uploaded_to_cloud = scrapy.Field()
    url = scrapy.Field()
    url_is_scraped = scrapy.Field()
    url_scraped_date = scrapy.Field()

    real_estate_type = scrapy.Field()
    condominio = scrapy.Field()
    iptu = scrapy.Field()
    size = scrapy.Field()
    rooms = scrapy.Field()
    bathrooms = scrapy.Field()
    garage_spaces = scrapy.Field()
    characteristics = scrapy.Field()

    old_price = scrapy.Field()

    neighbourhood = scrapy.Field()
    municipality = scrapy.Field()
    uf = scrapy.Field()
    ddd = scrapy.Field()

    category_name = scrapy.Field()
    professional_ad = scrapy.Field()
    is_featured = scrapy.Field()
    fixed_on_top = scrapy.Field()
    price_reduction_badge = scrapy.Field()
    has_real_estate_highlight = scrapy.Field()

    watched_state_fingerprint = scrapy.Field()


class AdItem(scrapy.Item):
    title = scrapy.Field()
    description = scrapy.Field()
    pricing = scrapy.Field()
    street_address = scrapy.Field()
    full_location = scrapy.Field()
    details = scrapy.Field()
    characteristics = scrapy.Field()
    date = scrapy.Field()
    breadcrumb = scrapy.Field()
    code = scrapy.Field()

    uploaded_to_cloud = scrapy.Field()
    scraped_date = scrapy.Field()
    url = scrapy.Field()

    real_estate_type = scrapy.Field()
    condominio = scrapy.Field()
    size = scrapy.Field()
    rooms = scrapy.Field()
    bathrooms = scrapy.Field()
    garage_spaces = scrapy.Field()

    neighbourhood = scrapy.Field()
    neighbourhood_id = scrapy.Field()
    municipality = scrapy.Field()
    municipality_id = scrapy.Field()
    uf = scrapy.Field()
    zipcode = scrapy.Field()
    lat = scrapy.Field()
    lng = scrapy.Field()
    ddd = scrapy.Field()
    zone = scrapy.Field()
    zone_id = scrapy.Field()
    region = scrapy.Field()

    watched_state_fingerprint = scrapy.Field()
