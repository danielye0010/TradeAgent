"""Fresh bounded public references using the existing strict canary parsers."""

import hashlib
import http.client
import re
import time
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from .broker import data, rows
from .canary import NASDAQ_DIRECTORY_URL, canary_known_action_check, nasdaq_common_reference
from .model import Halt

STATUS_URL = "https://www.nasdaqtrader.com/Trader.aspx?id=nasdaq-security-status-updates"
EX_DATE_URL = "https://www.nasdaqtrader.com/Trader.aspx?id=nasdaq-ex-date"
ISSUER_URLS = {"OPEN": "https://investor.opendoor.com/news-events/press-releases"}
HEADERS = {
    STATUS_URL: [
        "Effective Date",
        "Symbol",
        "Company Name",
        "Issue Event",
        "Downgrade Reason",
        "Old Financial Status",
        "New Financial Status",
    ],
    EX_DATE_URL: ["Issue Symbol", "Issuer Name", "Ex Date", "Record Date", "Pay Date"],
}


class ReferenceHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.links = [], []
        self.table, self.row, self.cell, self.link = None, None, None, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            if self.table is not None:
                raise Halt("nested reference table unsupported")
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []
        elif tag == "a":
            self.link = [attrs.get("href", ""), []]

    def handle_data(self, text):
        if self.cell is not None:
            self.cell.append(text)
        if self.link is not None:
            self.link[1].append(text)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None
        elif tag == "a" and self.link is not None:
            self.links.append((self.link[0], " ".join("".join(self.link[1]).split())))
            self.link = None


def action_rows(text, source):
    parser = ReferenceHTML()
    parser.feed(text)
    parser.close()
    expected = HEADERS[source]
    matches = [t for t in parser.tables if t and t[0] == expected]
    if len(matches) != 1 or parser.table is not None:
        raise Halt("available action-source schema unavailable/ambiguous")
    result = matches[0][1:]
    if any(len(row) != len(expected) for row in result):
        raise Halt("available action-source row incomplete")
    if not result:
        raise Halt("empty action source requires explicit verified no-record contract")
    return [dict(zip(expected, row, strict=True)) for row in result]


def fetch_public(url, timeout=15):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc not in {"www.nasdaqtrader.com", "investor.opendoor.com"}
        or parsed.username
        or parsed.fragment
    ):
        raise Halt("unapproved public reference origin")
    conn = http.client.HTTPSConnection(parsed.netloc, timeout=timeout)
    try:
        conn.request(
            "GET",
            parsed.path + ("?" + parsed.query if parsed.query else ""),
            headers={"User-Agent": "tradeagent-reference/0.1"},
        )
        response = conn.getresponse()
        raw = response.read(4 * 1024 * 1024 + 1)
        if response.status != 200 or len(raw) > 4 * 1024 * 1024:
            raise Halt("public reference unavailable; no redirect/retry")
        return raw.decode("utf-8-sig")
    except (OSError, UnicodeError, http.client.HTTPException) as exc:
        raise Halt("public reference unavailable; fail closed") from exc
    finally:
        conn.close()


class PublicReferenceReader:
    def __init__(self, bridge, config, clock=time.time, fetch=fetch_public, recorder=None):
        self.bridge, self.config, self.clock, self.fetch = bridge, config, clock, fetch
        self.recorder = recorder or (lambda kind, value: None)

    def __call__(self):
        today = datetime.fromtimestamp(self.clock(), ZoneInfo("America/New_York")).date()
        directory = self.fetch(NASDAQ_DIRECTORY_URL)
        received = self.clock()
        checks = []
        for source in (STATUS_URL, EX_DATE_URL):
            end = today if source == STATUS_URL else today + timedelta(days=14)
            query = urlencode({"from": today.strftime("%m/%d/%Y"), "to": end.strftime("%m/%d/%Y")})
            text = self.fetch(source + "&" + query)
            observed = self.clock()
            records = action_rows(text, source)
            date_field = "Effective Date" if source == STATUS_URL else "Ex Date"
            if any(
                not today <= datetime.strptime(row[date_field], "%Y-%m-%d").date() <= end
                for row in records
            ):
                raise Halt("action source returned records outside requested interval")
            checks.append(
                {
                    "source": source,
                    "asof": observed,
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "parsed": True,
                    "records": records,
                }
            )
        facts = {}
        for symbol in sorted(self.config.allowed_symbols):
            try:
                instruments = rows(
                    data(
                        self.bridge.read(
                            "search", {"query": symbol, "asset_type": "equity", "limit": 20}
                        )
                    ).get("results"),
                    "instruments",
                )
                fact, evidence = nasdaq_common_reference(
                    directory, instruments, symbol, received, self.clock()
                )
                if fact is None:
                    raise Halt("ordinary common classification unknown")
                bounded = [
                    {
                        "source": c["source"],
                        "asof": c["asof"],
                        "sha256": c["sha256"],
                        "parsed": True,
                        "symbol": symbol,
                        "entries": [
                            x
                            for x in c["records"]
                            if x.get("Symbol", x.get("Issue Symbol")) == symbol
                        ],
                    }
                    for c in checks
                ]
                if symbol in ISSUER_URLS:
                    issuer_text = self.fetch(ISSUER_URLS[symbol])
                    parser = ReferenceHTML()
                    parser.feed(issuer_text)
                    titles = [
                        title
                        for url, title in parser.links
                        if "/news-release-details/" in url and title
                    ]
                    if not titles:
                        raise Halt("issuer release index unavailable/unparsed")
                    entries = [
                        t
                        for t in titles
                        if re.search(r"split|merger|tender|delist|spin.off|reorganization", t, re.I)
                    ]
                    bounded.append(
                        {
                            "source": ISSUER_URLS[symbol],
                            "asof": self.clock(),
                            "sha256": hashlib.sha256(issuer_text.encode()).hexdigest(),
                            "parsed": True,
                            "symbol": symbol,
                            "entries": entries,
                        }
                    )
                facts[symbol] = canary_known_action_check(fact, bounded, self.clock())
                self.recorder(
                    "reference_evidence",
                    {"symbol": symbol, "classification": evidence, "checks": bounded},
                )
            except (Halt, ValueError) as exc:
                self.recorder("classification_rejected", {"symbol": symbol, "reason": str(exc)})
        return facts
