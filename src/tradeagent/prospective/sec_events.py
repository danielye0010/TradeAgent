"""Official SEC submissions and original documents, with declared fair access."""

import json
import re
import time
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, build_opener

from ..prospective.providers import NoRedirect
from ..research.domain import identity, timestamp
from ..research.opportunities import UNIVERSE
from .event_store import CONFIG

ETFS = frozenset("SPY QQQ IWM DIA XLF XLK XLE XLV TLT GLD".split())
ISSUERS = tuple(s for s in UNIVERSE if s not in ETFS)
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
RULES = {
    "2.02": (0, "earnings"),
    "1.01": (1, "material_agreement"),
    "2.01": (1, "acquisition"),
    "2.03": (1, "financing"),
    "2.05": (1, "restructuring"),
    "4.02": (1, "nonreliance"),
    "5.02": (2, "management"),
    "7.01": (2, "regulation_fd"),
    "8.01": (3, "other_material"),
    "3.01": (3, "listing"),
    "5.03": (3, "charter_action"),
}


class SECUnavailable(ValueError):
    def __init__(self, status, url):
        self.status, self.url = status, url
        super().__init__(f"SEC {status}: {url}")


class Client:
    def __init__(self, store, user_agent, *, opener=None, clock=time.time, sleep=time.sleep):
        if (
            not isinstance(user_agent, str)
            or len(user_agent) > 250
            or not re.search(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b", user_agent)
            or any(c in user_agent for c in "\r\n")
            or "noreply" in user_agent.lower()
        ):
            raise ValueError("genuine contact User-Agent required")
        self.store, self.user_agent = store, user_agent
        self.opener, self.clock, self.sleep = opener or build_opener(NoRedirect()), clock, sleep

    def get(self, url, *, max_bytes=8_000_000):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"www.sec.gov", "data.sec.gov"}:
            raise ValueError("only official HTTPS SEC sources allowed")
        if parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment:
            raise ValueError("invalid SEC source URL")
        now = self.clock()
        row = self.store.db.execute(
            "SELECT payload FROM event_records WHERE kind='sec_http' ORDER BY at DESC LIMIT 1"
        ).fetchone()
        last = [json.loads(row[0])] if row else []
        if last and now - last[0]["received_at"] < CONFIG["sec_minimum_request_seconds"]:
            self.sleep(CONFIG["sec_minimum_request_seconds"] - (now - last[0]["received_at"]))
        if (
            last
            and last[0]["status"] in {403, 429}
            and self.clock() - last[0]["received_at"] < 3600
        ):
            raise SECUnavailable("COOLDOWN_AFTER_403_OR_429", url)
        headers = {"User-Agent": self.user_agent, "Accept": "application/json,text/html,text/plain"}
        cached = self.store.db.execute("SELECT * FROM sec_cache WHERE url=?", (url,)).fetchone()
        if cached:
            if cached["etag"]:
                headers["If-None-Match"] = cached["etag"]
            if cached["modified"]:
                headers["If-Modified-Since"] = cached["modified"]
        started, status, body_hash = self.clock(), None, None
        try:
            with self.opener.open(Request(url, headers=headers), timeout=15) as response:
                status = response.status
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise SECUnavailable("BODY_LIMIT", url)
                body_hash = self.store.blob(body)
                with self.store.db:
                    self.store.db.execute(
                        "INSERT OR REPLACE INTO sec_cache VALUES(?,?,?,?,?)",
                        (
                            url,
                            response.headers.get("ETag"),
                            response.headers.get("Last-Modified"),
                            body_hash,
                            self.clock(),
                        ),
                    )
                return body, self.clock(), body_hash
        except HTTPError as exc:
            status = exc.code
            if status == 304 and cached and cached["body_hash"]:
                body_hash = cached["body_hash"]
                return (
                    (self.store.directory / "evidence" / (body_hash + ".bin")).read_bytes(),
                    self.clock(),
                    body_hash,
                )
            raise SECUnavailable(status, url) from None
        except OSError:
            status = "NETWORK_ERROR"
            raise SECUnavailable(status, url) from None
        finally:
            received = self.clock()
            self.store.add(
                "sec_http",
                identity([url, started, received]),
                {
                    "url": url,
                    "requested_at": started,
                    "received_at": received,
                    "status": status,
                    "body_hash": body_hash,
                },
            )


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.hidden, self.anchor = [], [], 0, None

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self.hidden += 1
        if tag == "a":
            self.anchor = [dict(attrs).get("href", ""), ""]

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "a" and self.anchor:
            self.links.append(tuple(self.anchor))
            self.anchor = None
        if tag in {"p", "div", "tr", "br"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data + " ")
        if self.anchor:
            self.anchor[1] += data

    @property
    def text(self):
        return " ".join("".join(self.parts).split())


def classify(form, items):
    if form.endswith("/A"):
        return 9, "amendment", False
    if form in {"10-K", "10-Q"}:
        return 1, "periodic_financial", True
    matches = [RULES[item] for item in items if item in RULES]
    return (*min(matches), True) if matches else (9, "other_8k", False)


def metadata(payload, symbol, discovered, activation):
    recent = payload.get("filings", {}).get("recent", {})
    keys = (
        "accessionNumber",
        "form",
        "primaryDocument",
        "acceptanceDateTime",
        "filingDate",
        "items",
    )
    n = len(recent.get("accessionNumber", []))
    if not all(len(recent.get(k, [])) == n for k in keys):
        raise ValueError("incomplete SEC recent filing columns")
    cik = int(payload["cik"])
    rows = []
    for i in range(n):
        form, accession = recent["form"][i], recent["accessionNumber"][i]
        if form not in {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A"}:
            continue
        document = recent["primaryDocument"][i]
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+", document
        ):
            raise ValueError("unsafe SEC filing identity")
        raw_acceptance = recent["acceptanceDateTime"][i]
        try:
            accepted = timestamp(raw_acceptance)
        except (ValueError, TypeError):
            accepted = None
        items = re.findall(r"\d\.\d{2}", recent["items"][i] or "")
        priority, category, material = classify(form, items)
        prospective = accepted is not None and activation <= accepted <= discovered
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{document}"
        )
        rows.append(
            {
                "id": identity([cik, accession]),
                "symbol": symbol,
                "cik": cik,
                "form": form,
                "accession": accession,
                "filing_date": recent["filingDate"][i],
                "accepted_at": accepted,
                "acceptance_raw": raw_acceptance,
                "first_discovered_at": discovered,
                "url": url,
                "items": items,
                "priority": priority,
                "category": category,
                "material": material,
                "prospective": prospective,
                "status": "MATERIAL_DETECTED" if material else "REJECTED",
                "rejection": "AMENDMENT"
                if category == "amendment"
                else "PREINSTALLATION_OR_UNKNOWN_PUBLICATION"
                if not prospective
                else "NONMATERIAL_CATEGORY"
                if not material
                else None,
            }
        )
    return rows


def content(client, event):
    raw, received, digest = client.get(event["url"], max_bytes=2_000_000)
    client.store.add(
        "retrieval",
        event["id"] + ":" + identity(event["url"]),
        {
            "event_id": event["id"],
            "url": event["url"],
            "first_retrieved_at": received,
            "sha256": digest,
        },
    )
    if raw.lstrip().startswith(b"%PDF"):
        raise SECUnavailable("UNSUPPORTED_PDF_TEXT", event["url"])
    parsed = Text()
    parsed.feed(raw.decode("utf-8", errors="replace"))
    sources = [
        {
            "url": event["url"],
            "sha256": digest,
            "text": parsed.text[:24000],
            "received_at": received,
        }
    ]
    base = event["url"].rsplit("/", 1)[0] + "/"
    links = []
    for href, label in parsed.links:
        if re.search(r"99[.\s_-]*1|ex(?:hibit)?[._-]*99", label + " " + href, re.I):
            url = urljoin(event["url"], href).split("#", 1)[0]
            if (
                url.startswith(base)
                and urlparse(url).hostname == "www.sec.gov"
                and url not in links
            ):
                links.append(url)
    for url in links[:1]:
        raw, at, digest = client.get(url, max_bytes=2_000_000)
        if raw.lstrip().startswith(b"%PDF"):
            raise SECUnavailable("UNSUPPORTED_PDF_TEXT", url)
        p = Text()
        p.feed(raw.decode("utf-8", errors="replace"))
        sources.append({"url": url, "sha256": digest, "text": p.text[:24000], "received_at": at})
    text = "\n".join(s["text"] for s in sources)
    if len(text) < 50:
        raise SECUnavailable("CONTENT_EMPTY", event["url"])
    return {
        "event_id": event["id"],
        "first_retrieved_at": min(
            r["first_retrieved_at"]
            for r in client.store.rows("retrieval")
            if r["event_id"] == event["id"]
        ),
        "available_at": client.clock(),
        "sources": sources,
        "content_hash": identity([{k: s[k] for k in ("url", "sha256")} for s in sources]),
        "extract": text[:48000],
        "truncated": any(len(s["text"]) == 24000 for s in sources),
    }


def poll(store, client, *, deadline=None):
    start = store.clock()
    slot = int(start // 900)
    if store.get("poll_started", slot):
        return {"status": "DUPLICATE", "slot": slot}
    store.add("poll_started", slot, {"slot": slot, "started_at": start})
    deadline = start + 90 if deadline is None else deadline
    covered, missing, new = [], [], 0
    try:
        raw, at, digest = client.get(TICKERS_URL)
        tickers = {r["ticker"]: int(r["cik_str"]) for r in json.loads(raw).values()}
        store.add(
            "issuer_mapping",
            digest,
            {
                "observed_at": at,
                "source": TICKERS_URL,
                "mapping": {s: tickers.get(s) for s in ISSUERS},
            },
        )
        for symbol in ISSUERS:
            if store.clock() >= deadline:
                missing.append({"symbol": symbol, "reason": "POLL_BUDGET"})
                continue
            if symbol not in tickers:
                missing.append({"symbol": symbol, "reason": "CIK_UNAVAILABLE"})
                continue
            try:
                raw, received, _ = client.get(SUBMISSIONS.format(cik=tickers[symbol]))
                payload = json.loads(raw)
                if int(payload["cik"]) != tickers[symbol]:
                    raise ValueError("SEC CIK differs from mapped issuer")
                for row in metadata(payload, symbol, received, store.activated_at):
                    new += store.add("event", row["id"], row)
                covered.append(symbol)
            except SECUnavailable as exc:
                missing.append({"symbol": symbol, "reason": str(exc)})
                if exc.status in {403, 429}:
                    raise
        pending = [
            e
            for e in store.rows("event")
            if e["prospective"]
            and e["material"]
            and not e["rejection"]
            and not store.get("content", e["id"])
        ]
        for event in sorted(
            pending, key=lambda e: (e["priority"], e["first_discovered_at"], e["id"])
        ):
            if store.clock() >= deadline:
                store.add(
                    "content_deferred",
                    f"{slot}:{event['id']}",
                    {"event_id": event["id"], "reason": "POLL_BUDGET"},
                )
                continue
            try:
                value = content(client, event)
                store.add("content", event["id"], value)
            except SECUnavailable as exc:
                store.add(
                    "content_failure",
                    f"{slot}:{event['id']}",
                    {"event_id": event["id"], "reason": str(exc)},
                )
                if exc.status in {403, 429}:
                    raise
        result = {
            "slot": slot,
            "status": "COMPLETE" if len(covered) == len(ISSUERS) else "PARTIAL",
            "started_at": start,
            "completed_at": store.clock(),
            "covered": covered,
            "missing": missing,
            "new_metadata_records": new,
        }
    except (SECUnavailable, ValueError, KeyError) as exc:
        result = {
            "slot": slot,
            "status": "BLOCKED",
            "started_at": start,
            "completed_at": store.clock(),
            "reason": str(exc),
            "covered": covered,
            "missing": missing,
        }
    store.add("poll", slot, result)
    return result
