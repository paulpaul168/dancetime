import concurrent.futures
import re
from datetime import datetime

import dateparser
import requests
from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta

from event import DanceEvent


def clean_name(name: str) -> str:
    # Some names start and end with a double quote for no reason
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]

    # Sometimes words inside names are in ALL-CAPS which is just ugly
    def deupperice(text: str) -> str:
        return text.capitalize() if text.isupper() else text

    name = " ".join(map(deupperice, name.split(" ")))

    # Some events end in ...
    if name.endswith("..."):
        name = name[:-3]

    # And some events should just always be renamed
    rename_table = {
        "Vienna Salsa Splash": "Salsa Splash",
    }

    if name in rename_table:
        name = rename_table[name]

    return name


# For the ends_at and price we need to do a second request to the ticketing website
# because only there it says when the event will end. That is a bit of
# work so we are doing it here in a separate function.
# Unfortunately, there are two versions of the ticketing website so we need to branch
# out into old and new here.
def add_fine_detail(event: DanceEvent) -> DanceEvent:
    response = requests.get(event.website, timeout=10)
    response.raise_for_status()
    html = response.text

    soup = BeautifulSoup(html, "html.parser")
    if soup.find("div", class_="event-start-time"):
        return add_fine_detail_new(event, soup)

    return add_fine_detail_old(event, soup)


def add_fine_detail_new(event: DanceEvent, soup: BeautifulSoup) -> DanceEvent:
    date_div = soup.find("div", class_="event-start-time")
    date_text: str = date_div.text.split("-")[-1].strip()
    event.ends_at = dateparser.parse(date_text, languages=["de", "en"])

    # We don't parse the year so, the year it might assume, can be off by one.
    while event.starts_at > event.ends_at:
        event.ends_at += relativedelta(days=1)

    # There is no good way to find prices so let's get all text that have the
    # right class and contain a euro sign.
    price_divs = soup.findAll("div", class_="fw-bold")
    price_texts = [text for d in price_divs if "€" in (text := d.text)]

    for price_text in price_texts:
        m = re.search(r"(\d+),(\d{2}) €", price_text)
        if m is None:
            continue
        price = int(m.groups(0)[0]) * 100 + int(m.groups(0)[1])

        if event.price_euro_cent is None or event.price_euro_cent > price:
            event.price_euro_cent = price

    # FIXME: Figure out how "ausgebucht" works in the new UI once that case
    # actually occurs on the website.

    return event


def add_fine_detail_old(event: DanceEvent, soup: BeautifulSoup) -> DanceEvent:
    date_span = soup.find("span", class_="end-date")
    date_text = date_span.text
    event.ends_at = dateparser.parse(date_text, languages=["de", "en"])

    # We don't parse the year so, the year it might assume, can be off by one.
    while event.starts_at > event.ends_at:
        event.ends_at += relativedelta(days=1)

    isAvailable = None
    price_items = soup.findAll(class_="ticket-price-cell")

    for price_item in price_items:
        m = re.search(r"€ (\d+),(\d{2})", price_item.text)
        if m is None:
            continue
        price = int(m.groups(0)[0]) * 100 + int(m.groups(0)[1])

        if event.price_euro_cent is None or event.price_euro_cent > price:
            event.price_euro_cent = price

        if isAvailable is None or isAvailable == False:
            isAvailable = not ("Ausgebucht" in price_item.text)

    if isAvailable is not None and not isAvailable:
        event.name += " [ausgebucht]"

    return event


# For ballsaal.at we need to download and parse html. This is more tedious than
# a JSON API but at least the format is very consistent.
def download_ballsaal() -> list[DanceEvent]:
    response = requests.get(
        "https://www.ballsaal.at/termine_tickets/?no_cache=1", timeout=10
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, features="html.parser")
    event_items = soup.find_all(class_="event")

    events = []
    for event in event_items:
        name = event.find(class_="name").text
        name = clean_name(name)
        description = event.find(class_="short-description").text
        date_string = event.find(class_="date").text
        url = event.find(class_="button")["href"]

        date = datetime.strptime(date_string[4:], "%d.%m.%Y, %H:%M Uhr")
        events.append(
            DanceEvent(
                starts_at=date,
                name=name,
                price_euro_cent=None,
                description=description,
                dancing_school="Ballsaal",
                website=url,
            )
        )

    # Add the ends_at to each event event if
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, len(events))
    ) as executor:
        events = list(executor.map(add_fine_detail, events))

    return events
