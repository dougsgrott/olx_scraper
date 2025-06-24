from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings


# SCRAPE = 'CATALOG'
SCRAPE = 'AD'

# if __name__ == '__main__':

# ---------------------------------------
#             Catalog scraping
# ---------------------------------------
if SCRAPE == 'CATALOG':
    from olx_scraper.spiders.catalog_spider import CatalogSpider

    # scrape_url = "https://www.olx.com.br/imoveis/aluguel/estado-es/norte-do-espirito-santo/vitoria?o=1"
    # scrape_url = "https://www.olx.com.br/imoveis/aluguel/estado-sc/norte-de-santa-catarina/bombinhas"
    # scrape_url = "https://www.olx.com.br/imoveis/aluguel/estado-sc/florianopolis-e-regiao/outras-cidades/imbituba"
    # scrape_url = "https://www.olx.com.br/imoveis/aluguel/estado-sc/florianopolis-e-regiao/outras-cidades/icara"
    scrape_url = "https://www.olx.com.br/imoveis/aluguel/estado-sc/florianopolis-e-regiao/grande-florianopolis"
    settings = get_project_settings()
    # You can override settings here if you want
    # settings.set('LOG_LEVEL', 'DEBUG')
    process = CrawlerProcess(settings)
    process.crawl(CatalogSpider, start_urls=scrape_url)
    process.start()
    print("Crawl finished!")



# ---------------------------------------
#               Ad scraping
# ---------------------------------------
if SCRAPE == 'AD':
    from olx_scraper.spiders.ad_spider import AdSpider

    # scrape_url = "https://es.olx.com.br/norte-do-espirito-santo/imoveis/kitnet-em-jadim-camburi-com-excelente-localizacao-shop-norte-sul-1412846000"
    # scrape_url = "https://es.olx.com.br/norte-do-espirito-santo/imoveis/alugo-flat-em-jardim-camburi-1414102698?rec=a&lis=vi_web%7C1020%7Cwho_saw_also_saw%7C0"

    settings = get_project_settings()
    # You can override settings here if you want
    # settings.set('LOG_LEVEL', 'DEBUG')
    process = CrawlerProcess(settings)
    # process.crawl(AdSpider, start_urls=scrape_url)
    process.crawl(AdSpider)
    process.start()
    print("Crawl finished!")