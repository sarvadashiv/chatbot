import logging

logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

def log_scraper_error(source, url, error):
    logging.error(
        f"[SCRAPER ERROR] Source={source} URL={url} Error={str(error)}"
    )