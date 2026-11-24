"""Parse the portal pages. No Home Assistant or network code here."""

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from html.parser import HTMLParser


class BadPage(ValueError):
    """The page is not what we expect."""


@dataclass(frozen=True)
class Bill:
    """One billed meter-read period; end is exclusive."""

    start: date
    end: date
    bill_date: date
    gallons: float
    reading_tg: float
    amount: float


@dataclass(frozen=True)
class Summary:
    """Account status."""

    balance: float
    past_due: float | None
    due_date: date | None
    last_payment: float | None
    last_payment_date: date | None
    account_type: str | None = None


class _Page(HTMLParser):
    """Collect tables by id, label/value paragraphs and links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: dict[str, list[list[str]]] = {}
        self.paragraphs: list[str] = []
        self.links: list[str] = []
        self.login_form = False
        self.csrf: str | None = None
        self._table: str | None = None
        self._cell: list[str] | None = None
        self._p: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("name") == "loginForm":
            self.login_form = True
        elif tag == "input" and a.get("name") == "_csrf":
            self.csrf = a.get("value")
        elif tag == "a":
            self.links.append(a.get("href") or "")
        elif tag == "table":
            self._table = a.get("id") or ""
            self.tables[self._table] = []
        elif tag == "tr" and self._table is not None:
            self.tables[self._table].append([])
        elif tag in ("td", "th") and self._table is not None:
            self._cell = []
        elif tag == "p":
            self._p = []

    def handle_endtag(self, tag):
        if tag == "table":
            self._table = None
        elif tag in ("td", "th") and self._cell is not None:
            if self._table is not None and self.tables[self._table]:
                self.tables[self._table][-1].append(_clean(self._cell))
            self._cell = None
        elif tag == "p" and self._p is not None:
            self.paragraphs.append(_clean(self._p))
            self._p = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._p is not None:
            self._p.append(data)


def _clean(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())


def parse(html: str) -> _Page:
    """Parse a page."""
    page = _Page()
    page.feed(html)
    page.close()
    return page


def _number(text: str) -> float:
    try:
        value = float(re.sub(r"[$,\s]", "", text))
    except ValueError as err:
        raise BadPage("bad number") from err
    if not math.isfinite(value) or abs(value) > 1_000_000:
        raise BadPage("implausible number")
    return value


def _date(text: str) -> date:
    for fmt in ("%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    raise BadPage("bad date")


def _rows(page: _Page, table: str, header: list[str]) -> list[list[str]]:
    rows = page.tables.get(table)
    if not rows or rows[0] != header:
        raise BadPage(f"{table} missing or changed")
    body = [r for r in rows[1:] if len(r) == len(header)]
    if len(body) > 500:
        raise BadPage("too many rows")
    return body


def consumption_path(page: _Page) -> str:
    """Return the only water consumption link."""
    paths = {
        p
        for p in page.links
        if re.fullmatch(r"/css/utility/consumptionChart/\d+/\d+", p)
    }
    if len(paths) != 1:
        raise BadPage(f"expected one water service, found {len(paths)}")
    return paths.pop()


def summary(page: _Page) -> Summary:
    """Read the account details page."""

    def field(label: str) -> str | None:
        for text in page.paragraphs:
            if text.startswith(label):
                return text[len(label) :].strip()
        return None

    def optional(label, pattern, convert):
        match = re.match(pattern, field(label) or "")
        return convert(match.group()) if match else None

    money, day = r"-?\$?-?[\d,]+\.\d\d", r"\d\d/\d\d/\d{4}"
    balance = optional("Current Balance:", money, _number)
    if balance is None:
        raise BadPage("balance missing")
    return Summary(
        balance,
        optional("Past Due Amount:", money, _number),
        optional("Bill Due Date:", day, _date),
        optional("Last Payment:", money, _number),
        optional("Last Payment Date:", day, _date),
        field("Account Type:"),
    )


def bills(transactions: _Page, reads: _Page) -> list[Bill]:
    """Join printed bills with their meter reads."""
    amounts: dict[date, float] = {}
    for row in _rows(
        transactions,
        "accountTransTable2",
        ["Date", "Type", "Due Date", "Amount", "Balance"],
    ):
        if row[1].startswith("Bill Print"):
            day = _date(row[0])
            amounts[day] = amounts.get(day, 0.0) + _number(row[3])
    result = []
    for row in _rows(
        reads,
        "meterReadingTable",
        [
            "Read Date",
            "Bill Date",
            "Days",
            "Read Amount",
            "Consumption ( 1,000 Gals )",
            "Average Daily",
        ],
    ):
        billed = _date(row[1])
        if billed not in amounts:
            continue  # older than the 12 months of transactions, or not posted yet
        end = _date(row[0])
        days = _number(row[2])
        if days != int(days) or not 1 <= days <= 200:
            raise BadPage("implausible period")
        result.append(
            Bill(
                end - timedelta(days=int(days)),
                end,
                billed,
                _number(row[4]) * 1000,
                _number(row[3]),
                amounts[billed],
            )
        )
    check(result)
    return sorted(result, key=lambda b: b.start)


def check(items: list[Bill]) -> None:
    """Raise BadPage on implausible data, before anything is stored."""
    previous = None
    for bill in sorted(items, key=lambda b: b.start):
        if not date(2000, 1, 1) <= bill.start < bill.end <= bill.bill_date:
            raise BadPage("implausible dates")
        if not 0 <= bill.gallons <= 1_000_000 or not 0 <= bill.amount <= 20_000:
            raise BadPage("implausible bill")
        if previous and bill.start < previous:
            raise BadPage("overlapping periods")
        previous = bill.end


def merge(stored: list[Bill], fetched: list[Bill]) -> list[Bill]:
    """Fetched bills replace stored ones; older stored history is kept."""
    if not fetched:
        return stored
    first = min(b.start for b in fetched)
    return [b for b in stored if b.end <= first] + fetched


def daily_rows(
    items: list[Bill], tz: tzinfo
) -> list[tuple[datetime, float, float, float, float]]:
    """Spread bills evenly: (local midnight, gallons, sum, dollars, sum)."""
    rows = []
    gal_sum = usd_sum = 0.0
    for bill in items:
        days = (bill.end - bill.start).days
        for i in range(days):
            frac = (i + 1) / days
            rows.append(
                (
                    datetime.combine(bill.start + timedelta(days=i), time(), tz),
                    bill.gallons / days,
                    gal_sum + bill.gallons * frac,
                    bill.amount / days,
                    usd_sum + bill.amount * frac,
                )
            )
        gal_sum += bill.gallons
        usd_sum += bill.amount
    return rows
