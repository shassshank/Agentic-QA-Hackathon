"""Deterministic page readers: DOM → structured observations.

Readers prefer test ids, then fall back to semantic structure (table headers, ARIA roles, labels), so a
renamed CSS class or reworded heading does not blind them. Raw text is always kept next to parsed
values so oracles and the report can quote exactly what was on screen.
"""

from __future__ import annotations

import re
from decimal import Decimal

from playwright.sync_api import Page
from pydantic import BaseModel

from .money import decimal_places, parse_money

# Identifier-like tokens: masked ("•••• 1042", "****1042", "xxxx-1042") or bare long digit runs.
_MASKED_ID = re.compile(r"[^\w\s$.,()\-:]{2,}\s?-?\d{2,6}|[xX*]{2,}\s?-?\d{2,6}")
_DIGIT_RUN = re.compile(r"\d{6,}")


class AccountView(BaseModel):
    name: str
    text: str  # full visible text of the card
    href: str | None = None
    type_label: str | None = None
    identifier_text: str | None = None
    balance_text: str | None = None
    balance: Decimal | None = None
    balance_decimals: int | None = None
    long_digit_runs: list[str] = []


class AccountDetail(BaseModel):
    path: str
    heading: str | None
    identifier_text: str | None
    balance_text: str | None
    balance: Decimal | None
    text: str


class TxRow(BaseModel):
    id: str | None = None
    cells: dict[str, str]  # header label → cell text
    occurred_at: str | None = None  # machine-readable instant from <time datetime>, if present
    date_text: str | None = None
    description: str | None = None
    type: str | None = None
    account: str | None = None
    amount_text: str | None = None
    amount: Decimal | None = None
    direction: str | None = None  # credit / debit / neutral, from sign or accessible label
    status: str | None = None

    def key(self) -> str:
        """Stable identity: DOM id when exposed, else the visible tuple."""
        return self.id or "|".join([self.occurred_at or self.date_text or "", self.description or "",
                                    self.amount_text or "", self.status or ""])


class TxTable(BaseModel):
    present: bool
    headers: list[str] = []
    rows: list[TxRow] = []
    count_text: str | None = None
    empty_text: str | None = None
    error_text: str | None = None


class FieldError(BaseModel):
    label: str
    message: str
    parts: list[str] = []  # each aria-describedby element separately (hint vs error)


class Feedback(BaseModel):
    alerts: list[str] = []
    statuses: list[str] = []
    field_errors: list[FieldError] = []


class SelectOption(BaseModel):
    value: str
    label: str


class Layout(BaseModel):
    viewport_width: int
    viewport_height: int
    scroll_width: int
    horizontal_overflow: bool
    offscreen_controls: list[str]
    clipped_controls: list[str]
    unlabeled_controls: list[str]


_COLUMNS = {
    "date": re.compile(r"date|time|when", re.I),
    "description": re.compile(r"description|memo|details|payee", re.I),
    "type": re.compile(r"^type|kind|category", re.I),
    "account": re.compile(r"account", re.I),
    "amount": re.compile(r"amount|value", re.I),
    "status": re.compile(r"status|state", re.I),
}

_TABLE_JS = r"""() => {
  const table = document.querySelector('[data-testid="transaction-table"]') || document.querySelector('main table');
  const text = (el) => el ? (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ') : null;
  const visible = (el) => !!el && el.getClientRects().length > 0;
  const status = [...document.querySelectorAll('[role="status"],[role="alert"]')].filter(visible);
  const countEl = document.querySelector('[data-testid="result-count"]') ||
    status.find(el => /\b(showing|\d+\s+(results?|transactions?))\b/i.test(text(el)));
  const emptyEl = document.querySelector('.state--empty') ||
    status.find(el => /\bno (transactions|results|records|matches)\b|nothing (to show|found)/i.test(text(el)));
  const errorEl = [...document.querySelectorAll('main [role="alert"]')].find(visible);
  if (!table) return { present: false, headers: [], rows: [], count: text(countEl), empty: text(emptyEl), error: text(errorEl) };
  const headers = [...table.querySelectorAll('thead th')].map(text);
  const rows = [...table.querySelectorAll('tbody tr')].map(tr => {
    const cells = [...tr.querySelectorAll('td')];
    const time = tr.querySelector('time[datetime]');
    return {
      id: tr.getAttribute('data-transaction-id') || tr.getAttribute('data-id') || null,
      cells: cells.map(td => text(td)),
      labels: cells.map(td => td.getAttribute('data-label')),
      time: time ? time.getAttribute('datetime') : null,
    };
  });
  return { present: true, headers, rows, count: text(countEl), empty: text(emptyEl), error: text(errorEl) };
}"""

_DIRECTION = [
    (re.compile(r"money in|credit|deposit(ed)?\b|received", re.I), "credit"),
    (re.compile(r"money out|debit|withdrawn|sent", re.I), "debit"),
    (re.compile(r"moved between|no change|internal", re.I), "neutral"),
]


def _column_for(header: str) -> str | None:
    for column, pattern in _COLUMNS.items():
        if pattern.search(header):
            return column
    return None


def read_transactions(page: Page) -> TxTable:
    raw = page.evaluate(_TABLE_JS)
    if not raw["present"]:
        return TxTable(present=False, count_text=raw["count"], empty_text=raw["empty"], error_text=raw["error"])
    headers: list[str] = raw["headers"]
    rows: list[TxRow] = []
    for r in raw["rows"]:
        labels = [lab or (headers[i] if i < len(headers) else f"col{i}") for i, lab in enumerate(r["labels"])]
        cells = dict(zip(labels, r["cells"], strict=False))
        fields: dict[str, str] = {}
        for label, value in cells.items():
            column = _column_for(label)
            if column and column not in fields:
                fields[column] = value
        amount_text = fields.get("amount")
        amount = parse_money(amount_text)
        direction = None
        for pattern, name in _DIRECTION:
            if amount_text and pattern.search(amount_text):
                direction = name
                break
        if direction is None and amount_text:
            direction = "debit" if re.search(r"[−-]\s*[$\d]", amount_text) else (
                "credit" if re.search(r"\+\s*[$\d]", amount_text) else "neutral")
        rows.append(TxRow(
            id=r["id"], cells=cells, occurred_at=r["time"], date_text=fields.get("date"),
            description=fields.get("description"), type=fields.get("type"), account=fields.get("account"),
            amount_text=amount_text, amount=amount, direction=direction, status=fields.get("status"),
        ))
    return TxTable(present=True, headers=headers, rows=rows, count_text=raw["count"], empty_text=raw["empty"],
                   error_text=raw["error"])


_ACCOUNTS_JS = r"""() => {
  const text = (el) => el ? (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ') : null;
  let cards = [...document.querySelectorAll('[data-testid="account-card"]')];
  if (!cards.length) cards = [...document.querySelectorAll('main a[href*="/accounts/"]')];
  return cards.map(card => ({
    text: text(card),
    href: card.getAttribute('href') || (card.querySelector('a[href]') || {}).getAttribute?.('href') || null,
    name: text(card.querySelector('h1,h2,h3,h4,[class*="name"]')),
    badge: text(card.querySelector('.badge,[class*="type"]')),
    balance: text(card.querySelector('[data-testid="account-balance"],[class*="balance"]:not([class*="label"])')),
  }));
}"""


def _identifier(text: str) -> str | None:
    match = _MASKED_ID.search(text)
    return match.group(0).strip() if match else None


def read_accounts(page: Page) -> list[AccountView]:
    views = []
    for raw in page.evaluate(_ACCOUNTS_JS):
        text = raw["text"] or ""
        type_label = raw["badge"]
        if not type_label:
            match = re.search(r"\b(checking|savings|current|deposit)\b", text, re.I)
            type_label = match.group(1) if match else None
        views.append(AccountView(
            name=raw["name"] or text.split(" ")[0],
            text=text,
            href=raw["href"],
            type_label=type_label,
            identifier_text=_identifier(text),
            balance_text=raw["balance"],
            balance=parse_money(raw["balance"]),
            balance_decimals=decimal_places(raw["balance"]),
            long_digit_runs=_DIGIT_RUN.findall(text.replace(",", "")),
        ))
    return views


_DETAIL_JS = r"""() => {
  const text = (el) => el ? (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ') : null;
  const main = document.querySelector('main') || document.body;
  const header = main.querySelector('.page-header') || main;
  return {
    heading: text(main.querySelector('h1')),
    identifier: text(main.querySelector('[data-testid="account-number"]')),
    balance: text(main.querySelector('[data-testid="account-balance"]')) ||
      text([...header.querySelectorAll('p,span,div')].find(el => /balance/i.test(text(el.previousElementSibling) || ''))),
    header: text(header),
    text: text(main),
  };
}"""


def read_account_detail(page: Page, path: str) -> AccountDetail:
    raw = page.evaluate(_DETAIL_JS)
    return AccountDetail(
        path=path,
        heading=raw["heading"],
        identifier_text=raw["identifier"] or _identifier(raw["header"] or ""),
        balance_text=raw["balance"],
        balance=parse_money(raw["balance"]),
        text=(raw["text"] or "")[:5000],
    )


_FEEDBACK_JS = r"""() => {
  const text = (el) => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
  const visible = (el) => el.getClientRects().length > 0;
  const labelOf = (el) => {
    if (el.labels && el.labels.length) return text(el.labels[0]);
    return el.getAttribute('aria-label') || el.name || el.id || el.tagName.toLowerCase();
  };
  const describedBy = (el) => (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean)
    .map(id => document.getElementById(id)).filter(Boolean).map(text);
  return {
    alerts: [...document.querySelectorAll('[role="alert"]')].filter(visible).map(text).filter(Boolean),
    statuses: [...document.querySelectorAll('[role="status"]')].filter(visible).map(text).filter(Boolean),
    fieldErrors: [...document.querySelectorAll('[aria-invalid="true"]')].map(el => ({
      label: labelOf(el), message: describedBy(el).join(' '), parts: describedBy(el),
    })),
  };
}"""

# Text that describes form fields before anything is submitted (format hints). Subtracted from what is shown
# after a rejection, so the error itself is judged rather than a hint that is always on screen.
_HINTS_JS = r"""() => {
  const text = (el) => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
  return [...document.querySelectorAll('input, select, textarea')].flatMap(el =>
    (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean)
      .map(id => document.getElementById(id)).filter(Boolean).map(text)).filter(Boolean);
}"""


def read_field_hints(page: Page) -> list[str]:
    return list(dict.fromkeys(page.evaluate(_HINTS_JS)))


def read_feedback(page: Page) -> Feedback:
    raw = page.evaluate(_FEEDBACK_JS)
    return Feedback(
        alerts=raw["alerts"],
        statuses=raw["statuses"],
        field_errors=[FieldError(**f) for f in raw["fieldErrors"]],
    )


def read_header(page: Page) -> dict:
    """Active-customer label and session controls in the page header."""
    return page.evaluate(r"""() => {
      const text = (el) => el ? (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ') : null;
      const header = document.querySelector('header') || document.body;
      const buttons = [...header.querySelectorAll('button,a')].map(text).filter(Boolean);
      return {
        customer: text(document.querySelector('[data-testid="active-customer"],header [class*=customer]')),
        text: text(header),
        controls: buttons,
      };
    }""")


def read_profile(page: Page) -> dict:
    return page.evaluate(r"""() => {
      const text = (el) => el ? (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ') : null;
      const main = document.querySelector('main') || document.body;
      const pairs = [...main.querySelectorAll('dt')].map(dt => [text(dt), text(dt.nextElementSibling)]);
      return {
        name: text(document.querySelector('[data-testid="profile-name"]') || [...main.querySelectorAll('dt')].find(e => /name/i.test(text(e)))?.nextElementSibling),
        email: text(document.querySelector('[data-testid="profile-email"]') || [...main.querySelectorAll('dt')].find(e => /email/i.test(text(e)))?.nextElementSibling),
        fields: Object.fromEntries(pairs),
        text: text(main),
      };
    }""")


def read_select_options(page: Page, label: str | re.Pattern[str]) -> list[SelectOption]:
    select = page.get_by_label(label).first
    raw = select.evaluate("el => [...el.options].map(o => ({ value: o.value, label: o.textContent.trim() }))")
    return [SelectOption(**o) for o in raw]


def read_transfer_balances(page: Page) -> list[dict]:
    """Balance list shown beside the transfer form (name/identifier text → balance)."""
    raw = page.evaluate(r"""() => [...document.querySelectorAll('[data-testid="transfer-balance"], .balance-list__value')].map(el => ({
      label: ((el.closest('li') || el.parentElement).innerText || '').replace(el.innerText, '').trim(),
      balance: el.innerText.trim(),
    }))""")
    return [{**r, "amount": parse_money(r["balance"])} for r in raw]


def read_page_text(page: Page) -> str:
    return page.evaluate("() => document.body ? document.body.innerText : ''")


def read_storage(page: Page) -> dict:
    """Keys and values in web storage: secrets must not be exposed there either."""
    return page.evaluate(r"""() => {
      const dump = (s) => { const o = {}; try { for (let i = 0; i < s.length; i++) { const k = s.key(i); o[k] = String(s.getItem(k)).slice(0, 500); } } catch (e) {} return o; };
      return { local: dump(window.localStorage), session: dump(window.sessionStorage), cookie: document.cookie };
    }""")


def read_layout(page: Page) -> Layout:
    raw = page.evaluate(r"""() => {
      const vw = window.innerWidth, vh = window.innerHeight;
      const name = (el) => (el.getAttribute('aria-label') || el.innerText || el.value || el.name || el.id || el.tagName).trim().slice(0, 60);
      const controls = [...document.querySelectorAll('a[href],button,input,select,textarea')]
        .filter(el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden')
        .filter(el => !el.closest('.skip-link') && !el.classList.contains('skip-link'));
      const offscreen = [], clipped = [], unlabeled = [];
      for (const el of controls) {
        const r = el.getBoundingClientRect();
        if (r.right > vw + 1 || r.left < -1) offscreen.push(name(el));
        if (el.scrollWidth > el.clientWidth + 2 && ['BUTTON', 'A'].includes(el.tagName) && getComputedStyle(el).overflow === 'hidden') clipped.push(name(el));
        const labelled = (el.labels && el.labels.length) || el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') ||
          (el.innerText || '').trim() || el.getAttribute('title') || el.type === 'submit' || el.type === 'hidden';
        if (!labelled) unlabeled.push(name(el));
      }
      return { vw, vh, sw: document.documentElement.scrollWidth, offscreen, clipped, unlabeled };
    }""")
    return Layout(
        viewport_width=raw["vw"], viewport_height=raw["vh"], scroll_width=raw["sw"],
        horizontal_overflow=raw["sw"] > raw["vw"] + 1, offscreen_controls=raw["offscreen"],
        clipped_controls=raw["clipped"], unlabeled_controls=raw["unlabeled"],
    )
