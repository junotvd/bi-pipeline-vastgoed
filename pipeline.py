"""
BI Pipeline — Immo Lietaer
Combineert scraping, cleaning en Supabase-insert in één script,
bedoeld om automatisch te draaien via GitHub Actions.

Env vars vereist (zet deze als GitHub Secrets):
  SUPABASE_URL
  SUPABASE_KEY
"""

import os
import re
import math
from datetime import datetime

import requests
import pandas as pd
from bs4 import BeautifulSoup
from supabase import create_client

URL = "https://immolietaer.be/nl/ons-aanbod/te-koop"
CONTAINER_SELECTOR = "a[href*='/nl/aanbod/']"


def scrape():
    response = requests.get(URL, timeout=20)
    response.encoding = "utf-8"
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    rows = []
    seen_hrefs = set()

    for item in soup.select(CONTAINER_SELECTOR):
        href = item.get("href")
        if not href or href in seen_hrefs:
            continue
        seen_hrefs.add(href)

        text = item.get_text(" ", strip=True)
        if not text:
            continue

        url_match = re.search(r"/nl/aanbod/(\d+)/([a-z\-]+)-te-koop-in-(\d{4})-([a-z\-]+)", href)
        listing_id = url_match.group(1) if url_match else None
        property_type = url_match.group(2) if url_match else None
        postcode = url_match.group(3) if url_match else None
        gemeente = url_match.group(4).replace("-", " ") if url_match else None

        price_match = re.search(r"€\s?([\d.]+)", text)
        price_text = price_match.group(0) if price_match else None

        ref_match = re.search(r"Ref\.\s*(\S+)", text)
        reference = ref_match.group(1) if ref_match else None

        dims_match = re.search(r"-\s?(\d+)\s?-\s?([\d.]+)\s?m²\s?-\s?([\d.]+)\s?m²", text)
        if dims_match:
            rooms, area_a, area_b = dims_match.groups()
        else:
            rooms, area_a, area_b = None, None, None

        rows.append({
            "listing_id": listing_id,
            "property_type": property_type,
            "postcode": postcode,
            "gemeente": gemeente,
            "price_text": price_text,
            "reference": reference,
            "rooms": rooms,
            "area_a_text": area_a,
            "area_b_text": area_b,
            "link": href,
            "raw_text": text,
        })

    return pd.DataFrame(rows)


def clean(df):
    df["price_eur"] = df["price_text"].str.replace(r"[^\d]", "", regex=True).astype(float)
    df["rooms"] = pd.to_numeric(df["rooms"], errors="coerce")
    df["living_area_m2"] = pd.to_numeric(df["area_b_text"], errors="coerce")
    df["property_type"] = df["property_type"].fillna("onbekend")
    df["gemeente"] = df["gemeente"].fillna("onbekend")
    df = df.dropna(subset=["price_eur"])
    df["price_tier"] = df["price_eur"].apply(
        lambda p: "Budget" if p < 250_000 else ("Mid-range" if p < 500_000 else "Premium")
    )
    df["scraped_at"] = datetime.now().isoformat()
    return df


def push_to_supabase(df):
    supabase_url = os.environ["SUPABASE_URL"]
    supabase_key = os.environ["SUPABASE_KEY"]
    supabase = create_client(supabase_url, supabase_key)

    records = df[[
        "listing_id", "property_type", "gemeente", "postcode",
        "price_eur", "rooms", "living_area_m2", "price_tier",
        "reference", "link", "scraped_at"
    ]].to_dict(orient="records")

    for r in records:
        for k, v in r.items():
            if isinstance(v, float) and math.isnan(v):
                r[k] = None

    supabase.table("immolietaer_listings").insert(records).execute()
    print(f"{len(records)} rijen verstuurd naar Supabase op {datetime.now().isoformat()}")


if __name__ == "__main__":
    raw_df = scrape()
    clean_df = clean(raw_df)
    push_to_supabase(clean_df)
