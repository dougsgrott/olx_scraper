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


# def strip_strings(input: List[str]):
#     # Strip leading and trailing whitespace/newlines
#     return [item.strip() for item in input]


# def remove_empty_strings(input: List[str]):
#     # Remove empty or insignificant strings
#     return [item for item in input if item]


# def normalize_spacing_strings(input: List[str]):
#     # Normalize spacing
#     return [re.sub(r'\s+', ' ', item) for item in input]


# def standardize_numeric_strings(input: List[str]):
#     # Standardize numeric values (Ensure consistent format for price)
#     # Keeping as a string, but properly formatted
#     return [item.replace('R$ ', 'R$') for item in input]


# def parse_title(input_list):
#     input_string = ''.join(input_list)
#     return input_string.strip()


# def parse_address(input_list):
#     input_string = ''.join(input_list)
#     return input_string.strip()


# def get_details_text(selector_list):
#     text_list = []
#     for html_string in selector_list:
#         soup = BeautifulSoup(html_string, 'html.parser')
#         text = soup.get_text()
#         text_list.append(text)
#     concat_text = '<br>'.join(text_list)
#     return concat_text


# def get_text_beautifulsoup(selector_list):
#     text_list = []
#     for html_string in selector_list:
#         soup = BeautifulSoup(html_string, 'html.parser')
#         text = soup.get_text()
#         text_list.append(text)
#     return text_list


# def replace_str_list(selector_list, old_str, new_str):
#     return [s.replace(old_str, new_str) for s in selector_list]


# def parse_details_text(input_string):
#     # 1. Remove all newline characters
#     parsed_string = input_string.replace('\n', '')

#     # 2. Remove all leading and trailing spaces
#     parsed_string = parsed_string.strip()

#     # 4. Remove any extra spaces around the units and words
#     parsed_string = re.sub(r'\s+', ' ', parsed_string)

#     # Remove extra spaces around commas
#     parsed_string = parsed_string.replace(' ,', ',')
#     parsed_string = parsed_string.replace(', ', ',')
#     parsed_string = parsed_string.replace(' <br>', '<br>')
#     parsed_string = parsed_string.replace('<br> ', '<br>')
#     return parsed_string


# def get_amenities_text(html_string):
#     if html_string == []:
#         return 'none'
#     try:
#         soup = BeautifulSoup(html_string[0], 'html.parser')
#         text = soup.get_text()
#     except:
#         text = 'error'
#     return text


# def parse_amenities_text(input_string):
#     # if '<br>' not in input_string:
#     #     # replace \n\n with <br>

#     parsed_string = re.sub(r'         ', '<br>', input_string)
#     parsed_string = re.sub(r'\n\n+', '<br>', parsed_string)

#     # 2. Remove all leading and trailing spaces
#     parsed_string = parsed_string.strip()

#     # remove spaces whitespace without using strip
#     parsed_string = re.sub(r'\s+', ' ', parsed_string)
#     parsed_string = parsed_string.replace(' <br>', '<br>')
#     parsed_string = parsed_string.replace('<br> ', '<br>')

#     # remove trainling and leading <br>
#     parsed_string = parsed_string.strip('<br>')

#     # replace contigous <br> with single <br>
#     parsed_string = re.sub(r'(<br>)+', '<br>', parsed_string)

#     return parsed_string


# def get_values_text(html_string):
#     if html_string == []:
#         return 'none'
#     try:
#         soup = BeautifulSoup(html_string[0], 'html.parser')
#         text = soup.get_text()
#     except:
#         text = 'error'
#     return text


# def parse_values_text(input_string):
#     if '<br>' not in input_string:
#         # replace \n\n with <br>
#         parsed_string = re.sub(r'\n\n+', '<br>', input_string)

#     # 2. Remove all leading and trailing spaces
#     parsed_string = parsed_string.strip()

#     # remove spaces whitespace without using strip
#     parsed_string = re.sub(r'\s+', ' ', parsed_string)
#     parsed_string = parsed_string.replace(' <br>', '<br>')
#     parsed_string = parsed_string.replace('<br> ', '<br>')

#     # remove trainling and leading <br>
#     parsed_string = parsed_string.strip('<br>')

#     return parsed_string


# def convert_to_str(input):
#     return str(input)


# def process_headcrumbs(input):
#     return ' -> '.join(input)


# def parse_price_selectors(input):
#     cleaned_data = strip_strings(input)
#     cleaned_data = normalize_spacing_strings(cleaned_data)
#     cleaned_data = remove_empty_strings(cleaned_data)
#     cleaned_data = '<br>'.join(cleaned_data)
#     return cleaned_data


# def parse_price_text(text):
#     # Default values
#     price_value = None
#     maintenance_fee = None
#     iptu_tax = None
#     price_is_undefined = 0

#     # Extract price
#     price_match = re.search(r'R\$ ([0-9\.]*)', text)
#     if price_match:
#         price_value = int(price_match.group(1).replace('.', ''))
#         price_is_undefined = 0
#     elif 'Sob consulta' in text:
#         price_value = None
#         price_is_undefined = 1

#     # Extract maintenance fee
#     cond_match = re.search(r'COND\. R\$<br>([0-9\.]*)', text)
#     if cond_match:
#         maintenance_fee = int(cond_match.group(1).replace('.', ''))
    
#     # Extract IPTU tax
#     iptu_match = re.search(r'IPTU R\$<br>([0-9\.]*)', text)
#     if iptu_match:
#         iptu_tax = int(iptu_match.group(1).replace('.', ''))
    
#     return {
#         'price_value': price_value,
#         'maintenance_fee': maintenance_fee,
#         'iptu_tax': iptu_tax,
#         'price_is_undefined': price_is_undefined,
#     }


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
            # Normalize key
            key = label#.lower().replace(' ', '_')
            # Extract first number (integer or float)
            match = re.search(r'\d+(?:[\.,]\d+)?', label)
            if match:
                value_str = match.group(0).replace(',', '.')
            #     value = float(value_str) if '.' in value_str else int(value_str)
            # else:
            #     value = label # Fallback: store original string if no number found
            details_dict[key] = value_str
        return details_dict

    @staticmethod
    def process_badges(input):
        soup = BeautifulSoup(input[0], 'html.parser')
        badges = []
        for badge in soup.select('.olx-adcard__badges span'):
            label = badge.get('aria-label', '').strip() or badge.get_text(strip=True)
            if label:
                badges.append(label)
        return [badges]



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
        print(price_info_dict)
        return price_info_dict

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
        print(info_dict)
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
    # bottom_body = scrapy.Field(input_processor=process_bottom_body)
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
        return pricing_dict

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
        return details

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
        return inverted

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
    title = scrapy.Field(input_processor=process_title, output_processor=TakeFirst())
    description = scrapy.Field(input_processor=process_description, output_processor=TakeFirst())
    pricing = scrapy.Field(input_processor=process_pricing, output_processor=TakeFirst())
    street_address = scrapy.Field(input_processor=process_street_address, output_processor=TakeFirst())
    full_location = scrapy.Field(input_processor=process_full_location, output_processor=TakeFirst())
    details = scrapy.Field(input_processor=process_details, output_processor=TakeFirst())
    characteristics = scrapy.Field(input_processor=process_characteristics, output_processor=TakeFirst())
    date = scrapy.Field(input_processor=process_date, output_processor=TakeFirst())
    breadcrumb = scrapy.Field(input_processor=process_breadcrumb, output_processor=TakeFirst())
    code = scrapy.Field(input_processor=process_code, output_processor=TakeFirst())

    uploaded_to_cloud = scrapy.Field()
    scraped_date = scrapy.Field()
    url = scrapy.Field(output_processor=TakeFirst())

    # url = scrapy.Field(input_processor=process_url, output_processor=TakeFirst())
    # is_scraped = scrapy.Field(input_processor=process_is_scraped, output_processor=TakeFirst())
    # scraped_date = scrapy.Field(input_processor=process_scraped_date, output_processor=TakeFirst())
