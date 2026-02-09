# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

import scrapy
from itemloaders.processors import Compose, TakeFirst, Join, MapCompose
import re
from w3lib.html import remove_tags
from bs4 import BeautifulSoup
from typing import List


class CatalogItem(scrapy.Item):

    @staticmethod
    def process_type(input):
        return input

    @staticmethod
    def process_title(input):
        cleaned_data = input
        return cleaned_data

    @staticmethod
    def process_details(input):
        soup = BeautifulSoup(input[0], 'html.parser')
        details_dict = {}
        for detail in soup.select('.olx-adcard__detail'):
            label = detail.get('aria-label', '').strip()
            if not label:
                continue
            value_str = detail.text.strip()
            details_dict[label] = value_str
        return str(details_dict)

    @staticmethod
    def process_badges(input):
        soup = BeautifulSoup(input[0], 'html.parser')
        badges = []
        for badge in soup.select('.olx-adcard__badges span'):
            label = badge.get('aria-label', '').strip() or badge.get_text(strip=True)
            if label:
                badges.append(label)
        return str(badges)



    @staticmethod
    def process_pricing(input):
        soup = BeautifulSoup(input[0], 'html.parser')
        price_info_dict = {}
        # Main price
        price_tag = soup.select_one('.olx-adcard__price')
        if price_tag:
            price_info_dict['price'] = price_tag.get_text(strip=True)
        # Additional price details (e.g., IPTU, Condomínio)
        for div in soup.select('.olx-adcard__price-info'):
            text = div.get_text(strip=True)
            if ' ' in text:
                key, value = text.split(' ', 1)
                price_info_dict[key.lower()] = value
        return str(price_info_dict)

    @staticmethod
    def process_bottom_body(input):
        soup = BeautifulSoup(input[0], 'html.parser')
        info_dict = {}
        # Location
        location_tag = soup.select_one('.olx-adcard__location')
        if location_tag:
            info_dict['location'] = location_tag.get_text(strip=True)
        # Date
        date_tag = soup.select_one('.olx-adcard__date')
        if date_tag:
            info_dict['date'] = date_tag.get_text(strip=True)
        # print(info_dict)
        return info_dict

    @staticmethod
    def process_location(input):
        output_dict = CatalogItem.process_bottom_body(input)
        if 'location' in output_dict:
            location = output_dict['location']
        else:
            location = None
        return location

    @staticmethod
    def process_date(input):
        output_dict = CatalogItem.process_bottom_body(input)
        if 'date' in output_dict:
            date = output_dict['date']
        else:
            date = None
        return date

    @staticmethod
    def process_code(input):
        code = input[0].split('-')[-1]
        return code

    uid = scrapy.Field()
    title = scrapy.Field()
    details = scrapy.Field(input_processor=process_details)
    badges = scrapy.Field(input_processor=process_badges)
    pricing = scrapy.Field(input_processor=process_pricing)
    date = scrapy.Field(input_processor=process_date)
    location = scrapy.Field(input_processor=process_location)
    code = scrapy.Field(input_processor=process_code)
    scraped_date = scrapy.Field()
    uploaded_to_cloud = scrapy.Field()
    url = scrapy.Field()
    url_is_scraped = scrapy.Field()
    url_scraped_date = scrapy.Field()
        

class StatusItem(scrapy.Item):
    type = scrapy.Field()
    title = scrapy.Field()
    code = scrapy.Field()
    url = scrapy.Field()
    is_scraped = scrapy.Field()
    scraped_date = scrapy.Field()


class AdItem(scrapy.Item):

    @staticmethod
    def process_title(input):
        soup = BeautifulSoup(input[0], 'html.parser')
        title_tag = soup.find("span", class_="olx-text--title-medium")
        title_text = title_tag.get_text(strip=True) if title_tag else None
        return title_text

    @staticmethod
    def process_description(input):
        soup = BeautifulSoup(input[0], 'html.parser')
        description_tag = soup.find("span", class_="olx-text--body-medium")
        description_text = description_tag.get_text(strip=True) if description_tag else None
        return description_text

    @staticmethod
    def process_pricing(input):
        soup = BeautifulSoup(input[0], 'html.parser')
        pricing_dict = {}

        # Extract original price (usually the first <span> with olx-text--body-medium)
        original_price_tag = soup.find("span", class_="olx-text--body-medium")
        original_price = original_price_tag.get_text(strip=True) if original_price_tag else None

        # Extract current price (second price, larger text)
        current_price_tag = soup.find("span", class_="olx-text--title-large")
        current_price = current_price_tag.get_text(strip=True) if current_price_tag else None

        # Extract listing type (e.g., Aluguel)
        badge_tag = soup.find("span", class_="olx-badge")
        listing_type = badge_tag.get_text(strip=True) if badge_tag else None

        if original_price != None:
            pricing_dict['original_price'] = original_price
        if current_price != None:
            pricing_dict['current_price'] = current_price
        if listing_type != None:
            pricing_dict['listing_type'] = listing_type
        return str(pricing_dict)

    @staticmethod
    def process_details(input):
        soup = BeautifulSoup(input[0], 'html.parser')
        # Find the container that holds the details (based on id or known structure)
        details_section = soup.find('div', id='details')
        # Initialize dictionary to hold extracted details
        details = {}
        if details_section:
            # Look for each detail block (assuming each detail is a div with two spans or a span and a link)
            for block in details_section.find_all('div'):
                texts = block.find_all(['span', 'a'])
                if len(texts) >= 2:
                    label = texts[0].get_text(strip=True)
                    value = texts[1].get_text(strip=True)
                    details[label] = value
        return str(details)

    @staticmethod
    def process_location(input):
        soup = BeautifulSoup(input[0], "html.parser")
        # Find the div that contains location text
        location_container = soup.find("div", id="location")
        # Extract address lines (usually inside <span> tags)
        address_spans = location_container.find_all("span", class_="olx-text")
        # Get text content from the spans (remove empty ones and strip)
        location_lines = [span.get_text(strip=True) for span in address_spans if span.get_text(strip=True)]
        # Extract individual lines
        street_address = location_lines[1] if len(location_lines) > 1 else None
        full_location = location_lines[2] if len(location_lines) > 2 else None
        return {
            'street_address': street_address,
            'full_location': full_location
        }

    @staticmethod
    def process_street_address(input):
        output_dict = AdItem.process_location(input)
        if 'street_address' in output_dict:
            street_address = output_dict['street_address']
        else:
            street_address = None
        return street_address

    @staticmethod
    def process_full_location(input):
        output_dict = AdItem.process_location(input)
        if 'full_location' in output_dict:
            full_location = output_dict['full_location']
        else:
            full_location = None
        return full_location

    @staticmethod
    def process_characteristics(input):
        inverted = {}
        for key, values in input[0].items():
            for value in values:
                if value not in inverted:
                    inverted[value] = key
        return str(inverted)

    @staticmethod
    def process_date(input):
        return input

    @staticmethod
    def process_code(input):
        return next((s for s in input if s.isdigit()), None)

    @staticmethod
    def process_breadcrumb(input):
        soup = BeautifulSoup(input[0], "html.parser")
        breadcrumb_items = soup.select("nav[data-ds-component='DS-Breadcrumb'] a")
        breadcrumb_texts = [item.get_text(strip=True) for item in breadcrumb_items]
        output_text = " > ".join(breadcrumb_texts)
        return output_text

    uid = scrapy.Field()
    title = scrapy.Field(input_processor=process_title)
    description = scrapy.Field(input_processor=process_description)
    pricing = scrapy.Field(input_processor=process_pricing)
    street_address = scrapy.Field(input_processor=process_street_address)
    full_location = scrapy.Field(input_processor=process_full_location)
    details = scrapy.Field(input_processor=process_details)
    characteristics = scrapy.Field(input_processor=process_characteristics)
    date = scrapy.Field(input_processor=process_date)
    breadcrumb = scrapy.Field(input_processor=process_breadcrumb)
    code = scrapy.Field(input_processor=process_code)

    uploaded_to_cloud = scrapy.Field()
    scraped_date = scrapy.Field()
    url = scrapy.Field(output_processor=TakeFirst())
