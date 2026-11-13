"""Deterministic, auditable mutual-fund calculations for Smriti Finance.

All monetary and unit calculations use Decimal. SQLite stores the original decimal
text so values are not silently rounded through binary floating point.
"""

import csv
import hashlib
import io
import json
import re
import sqlite3
import urllib.request
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext


getcontext().prec = 34
AMFI_NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
TRANSACTION_TYPES = {
    "purchase": 1,
    "sip": 1,
    "switch_in": 1,
    "reinvestment": 1,
    "redemption": -1,
    "switch_out": -1,
    "dividend": 0,
    "distribution": 0,
}


def decimal_text(value, label, *, positive=False, allow_zero=False):
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"{label} must be a valid number.")
    if not result.is_finite() or (positive and result < (Decimal("0") if allow_zero else Decimal("0.000000000001"))):
        raise ValueError(f"{label} must be positive.")
    return format(result, "f")


def money(value):
    return Decimal(value or "0").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(path):
    with connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS mf_schemes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amfi_code TEXT UNIQUE,
                name TEXT NOT NULL,
                isin_growth TEXT,
                isin_reinvestment TEXT,
                plan TEXT NOT NULL DEFAULT 'unknown',
                option_name TEXT NOT NULL DEFAULT 'unknown',
                category TEXT NOT NULL DEFAULT 'Unclassified',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mf_folios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folio_number TEXT NOT NULL,
                scheme_id INTEGER NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(folio_number, scheme_id),
                FOREIGN KEY(scheme_id) REFERENCES mf_schemes(id)
            );
            CREATE TABLE IF NOT EXISTS mf_import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT NOT NULL,
                file_hash TEXT NOT NULL UNIQUE,
                format_name TEXT NOT NULL,
                status TEXT NOT NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                accepted_count INTEGER NOT NULL DEFAULT 0,
                rejected_count INTEGER NOT NULL DEFAULT 0,
                report_json TEXT NOT NULL DEFAULT '{}',
                imported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mf_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scheme_id INTEGER NOT NULL,
                folio_id INTEGER,
                import_batch_id INTEGER,
                transaction_date TEXT NOT NULL,
                transaction_type TEXT NOT NULL,
                amount TEXT NOT NULL,
                units TEXT NOT NULL,
                nav TEXT,
                description TEXT NOT NULL DEFAULT '',
                source_fingerprint TEXT NOT NULL UNIQUE,
                raw_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(scheme_id) REFERENCES mf_schemes(id),
                FOREIGN KEY(folio_id) REFERENCES mf_folios(id),
                FOREIGN KEY(import_batch_id) REFERENCES mf_import_batches(id)
            );
            CREATE INDEX IF NOT EXISTS idx_mf_transactions_scheme_date
                ON mf_transactions(scheme_id, transaction_date, id);
            CREATE TABLE IF NOT EXISTS mf_nav_history (
                scheme_id INTEGER NOT NULL,
                nav_date TEXT NOT NULL,
                nav TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'AMFI',
                fetched_at TEXT NOT NULL,
                PRIMARY KEY(scheme_id, nav_date),
                FOREIGN KEY(scheme_id) REFERENCES mf_schemes(id)
            );
            CREATE TABLE IF NOT EXISTS mf_portfolio_snapshots (
                snapshot_date TEXT PRIMARY KEY,
                invested_cost TEXT NOT NULL,
                market_value TEXT NOT NULL,
                unrealized_gain TEXT NOT NULL,
                calculation_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mf_agent_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_type TEXT NOT NULL,
                valuation_date TEXT,
                evidence_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def normalize_type(value):
    text = re.sub(r"[^a-z]+", "_", str(value or "").strip().lower()).strip("_")
    aliases = {
        "buy": "purchase", "lumpsum": "purchase", "lump_sum": "purchase",
        "systematic_investment": "sip", "systematic_investment_plan": "sip",
        "sell": "redemption", "redeem": "redemption", "switchin": "switch_in",
        "switchout": "switch_out", "dividend_payout": "dividend",
    }
    return aliases.get(text, text)


def validate_date(value):
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass
    raise ValueError("transaction_date must be a recognizable date.")


def transaction_payload(row):
    lowered = {str(key).strip().lower().replace(" ", "_"): value for key, value in row.items()}
    required = ("transaction_date", "scheme_name", "transaction_type", "amount", "units")
    missing = [key for key in required if not str(lowered.get(key, "")).strip()]
    if missing:
        raise ValueError("Missing " + ", ".join(missing) + ".")
    tx_type = normalize_type(lowered["transaction_type"])
    if tx_type not in TRANSACTION_TYPES:
        raise ValueError(f"Unsupported transaction_type: {tx_type or 'blank'}.")
    amount = decimal_text(lowered["amount"], "amount", positive=True, allow_zero=tx_type not in {"purchase", "sip", "switch_in", "redemption", "switch_out"})
    units = decimal_text(lowered["units"], "units", positive=True, allow_zero=TRANSACTION_TYPES[tx_type] == 0)
    nav_value = str(lowered.get("nav", "")).strip()
    nav = decimal_text(nav_value, "nav", positive=True) if nav_value else None
    return {
        "transaction_date": validate_date(lowered["transaction_date"]),
        "scheme_name": str(lowered["scheme_name"]).strip()[:500],
        "amfi_code": re.sub(r"\D", "", str(lowered.get("amfi_code", "")))[:20] or None,
        "folio": str(lowered.get("folio", lowered.get("folio_number", ""))).strip()[:100],
        "transaction_type": tx_type,
        "amount": amount,
        "units": units,
        "nav": nav,
        "description": str(lowered.get("description", "")).strip()[:1000],
    }


def fingerprint(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_or_create_scheme(connection, payload, timestamp):
    row = None
    if payload.get("amfi_code"):
        row = connection.execute("SELECT * FROM mf_schemes WHERE amfi_code=?", (payload["amfi_code"],)).fetchone()
    if not row:
        row = connection.execute("SELECT * FROM mf_schemes WHERE lower(name)=lower(?)", (payload["scheme_name"],)).fetchone()
    if row:
        return row
    cursor = connection.execute(
        "INSERT INTO mf_schemes(amfi_code,name,created_at,updated_at) VALUES(?,?,?,?)",
        (payload.get("amfi_code"), payload["scheme_name"], timestamp, timestamp),
    )
    return connection.execute("SELECT * FROM mf_schemes WHERE id=?", (cursor.lastrowid,)).fetchone()


def get_or_create_folio(connection, folio_number, scheme_id, timestamp):
    if not folio_number:
        return None
    connection.execute(
        "INSERT OR IGNORE INTO mf_folios(folio_number,scheme_id,created_at) VALUES(?,?,?)",
        (folio_number, scheme_id, timestamp),
    )
    return connection.execute(
        "SELECT id FROM mf_folios WHERE folio_number=? AND scheme_id=?",
        (folio_number, scheme_id),
    ).fetchone()["id"]


def import_csv(path, file_bytes, file_name):
    digest = hashlib.sha256(file_bytes).hexdigest()
    timestamp = datetime.now(timezone.utc).isoformat()
    with connect(path) as connection:
        existing = connection.execute("SELECT * FROM mf_import_batches WHERE file_hash=?", (digest,)).fetchone()
        if existing:
            result = dict(existing)
            result["duplicate"] = True
            result["report"] = json.loads(result.pop("report_json"))
            return result
    try:
        decoded = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("The transaction CSV must use UTF-8 encoding.")
    reader = csv.DictReader(io.StringIO(decoded))
    if not reader.fieldnames:
        raise ValueError("The CSV has no header row.")
    accepted, rejected = [], []
    seen = set()
    for number, row in enumerate(reader, start=2):
        try:
            payload = transaction_payload(row)
            item_hash = fingerprint(payload)
            if item_hash in seen:
                raise ValueError("Duplicate row in this file.")
            seen.add(item_hash)
            accepted.append((payload, item_hash))
        except ValueError as exc:
            rejected.append({"row": number, "error": str(exc)})
    status = "committed" if accepted and not rejected else "partial" if accepted else "rejected"
    report = {"errors": rejected, "message": "Rows with errors were not imported." if rejected else "All rows validated."}
    with connect(path) as connection:
        cursor = connection.execute(
            "INSERT INTO mf_import_batches(file_name,file_hash,format_name,status,row_count,accepted_count,rejected_count,report_json,imported_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (file_name[:255], digest, "smriti-mf-csv-v1", status, len(accepted) + len(rejected), len(accepted), len(rejected), json.dumps(report), timestamp),
        )
        batch_id = cursor.lastrowid
        inserted, duplicates = 0, 0
        for payload, item_hash in accepted:
            scheme = get_or_create_scheme(connection, payload, timestamp)
            folio_id = get_or_create_folio(connection, payload["folio"], scheme["id"], timestamp)
            try:
                connection.execute(
                    "INSERT INTO mf_transactions(scheme_id,folio_id,import_batch_id,transaction_date,transaction_type,amount,units,nav,description,source_fingerprint,raw_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (scheme["id"], folio_id, batch_id, payload["transaction_date"], payload["transaction_type"], payload["amount"], payload["units"], payload["nav"], payload["description"], item_hash, json.dumps(payload, ensure_ascii=False), timestamp),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                duplicates += 1
        report.update({"inserted": inserted, "existing_transactions": duplicates})
        connection.execute(
            "UPDATE mf_import_batches SET accepted_count=?,report_json=? WHERE id=?",
            (inserted, json.dumps(report), batch_id),
        )
    return {"id": batch_id, "status": status, "row_count": len(accepted) + len(rejected), "accepted_count": inserted, "rejected_count": len(rejected), "duplicate": False, "report": report}


def add_transaction(path, body):
    payload = transaction_payload(body)
    item_hash = fingerprint(payload)
    timestamp = datetime.now(timezone.utc).isoformat()
    with connect(path) as connection:
        scheme = get_or_create_scheme(connection, payload, timestamp)
        folio_id = get_or_create_folio(connection, payload["folio"], scheme["id"], timestamp)
        try:
            cursor = connection.execute(
                "INSERT INTO mf_transactions(scheme_id,folio_id,transaction_date,transaction_type,amount,units,nav,description,source_fingerprint,raw_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (scheme["id"], folio_id, payload["transaction_date"], payload["transaction_type"], payload["amount"], payload["units"], payload["nav"], payload["description"], item_hash, json.dumps(payload), timestamp),
            )
        except sqlite3.IntegrityError:
            raise ValueError("This transaction already exists.")
        return cursor.lastrowid


def parse_amfi(text):
    rows = []
    category = "Unclassified"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        fields = [part.strip() for part in line.split(";")]
        if len(fields) >= 6 and fields[0].isdigit():
            current_format = len(fields) >= 8
            try:
                nav_index, date_index = (6, 7) if current_format else (4, 5)
                nav = decimal_text(fields[nav_index], "NAV", positive=True)
                nav_date = datetime.strptime(fields[date_index], "%d-%b-%Y").date().isoformat()
            except ValueError:
                continue
            rows.append({
                "amfi_code": fields[0], "isin_growth": fields[1] if fields[1] not in {"", "-"} else None,
                "isin_reinvestment": fields[2] if fields[2] not in {"", "-"} else None,
                "name": fields[3], "plan": fields[4] if current_format else "unknown",
                "option_name": fields[5] if current_format else "unknown", "nav": nav,
                "nav_date": nav_date, "category": category,
            })
        elif ";" not in line and re.match(r"^(Open Ended|Close Ended|Interval)", line, re.I):
            category = line[:200]
    return rows


def refresh_nav(path, timeout=30):
    request = urllib.request.Request(AMFI_NAV_URL, headers={"User-Agent": "Smriti/0.1 personal portfolio tracker"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read(8_000_000).decode("utf-8", errors="replace")
    records = parse_amfi(text)
    if not records:
        raise ValueError("AMFI returned no usable NAV records.")
    timestamp = datetime.now(timezone.utc).isoformat()
    updated = 0
    with connect(path) as connection:
        tracked = {row["amfi_code"]: row["id"] for row in connection.execute("SELECT id,amfi_code FROM mf_schemes WHERE amfi_code IS NOT NULL")}
        for item in records:
            scheme_id = tracked.get(item["amfi_code"])
            if not scheme_id:
                continue
            connection.execute(
                "UPDATE mf_schemes SET name=?,isin_growth=?,isin_reinvestment=?,plan=?,option_name=?,category=?,updated_at=? WHERE id=?",
                (item["name"], item["isin_growth"], item["isin_reinvestment"], item["plan"], item["option_name"], item["category"], timestamp, scheme_id),
            )
            connection.execute(
                "INSERT INTO mf_nav_history(scheme_id,nav_date,nav,source,fetched_at) VALUES(?,?,?,?,?) ON CONFLICT(scheme_id,nav_date) DO UPDATE SET nav=excluded.nav,fetched_at=excluded.fetched_at",
                (scheme_id, item["nav_date"], item["nav"], "AMFI", timestamp),
            )
            updated += 1
    return {"downloaded": len(records), "tracked_updated": updated, "fetched_at": timestamp}


def xirr(cashflows):
    if not cashflows or not any(value < 0 for _, value in cashflows) or not any(value > 0 for _, value in cashflows):
        return None
    start = min(day for day, _ in cashflows)
    def npv(rate):
        return sum(float(value) / ((1.0 + rate) ** ((day - start).days / 365.0)) for day, value in cashflows)
    low, high = -0.9999, 10.0
    try:
        low_value, high_value = npv(low), npv(high)
        while low_value * high_value > 0 and high < 1_000_000:
            high *= 10
            high_value = npv(high)
        if low_value * high_value > 0:
            return None
        for _ in range(200):
            middle = (low + high) / 2
            value = npv(middle)
            if abs(value) < 1e-7:
                return middle
            if low_value * value <= 0:
                high = middle
            else:
                low, low_value = middle, value
        return (low + high) / 2
    except (OverflowError, ZeroDivisionError, ValueError):
        return None


def portfolio(path, valuation_date=None):
    valuation = date.fromisoformat(valuation_date) if valuation_date else date.today()
    with connect(path) as connection:
        schemes = connection.execute("SELECT * FROM mf_schemes ORDER BY name").fetchall()
        transactions = connection.execute("SELECT * FROM mf_transactions WHERE transaction_date<=? ORDER BY transaction_date,id", (valuation.isoformat(),)).fetchall()
        latest_navs = connection.execute(
            "SELECT n.* FROM mf_nav_history n JOIN (SELECT scheme_id,MAX(nav_date) nav_date FROM mf_nav_history WHERE nav_date<=? GROUP BY scheme_id) x ON x.scheme_id=n.scheme_id AND x.nav_date=n.nav_date",
            (valuation.isoformat(),),
        ).fetchall()
    by_scheme = defaultdict(list)
    for row in transactions:
        by_scheme[row["scheme_id"]].append(row)
    navs = {row["scheme_id"]: row for row in latest_navs}
    holdings, all_cashflows, reconciliation_issues = [], [], []
    for scheme in schemes:
        lots, realized = deque(), Decimal("0")
        for tx in by_scheme[scheme["id"]]:
            tx_date = date.fromisoformat(tx["transaction_date"])
            amount, units = Decimal(tx["amount"]), Decimal(tx["units"])
            direction = TRANSACTION_TYPES.get(tx["transaction_type"], 0)
            if direction > 0:
                unit_cost = amount / units if units else Decimal("0")
                lots.append([units, unit_cost])
                all_cashflows.append((tx_date, -amount))
            elif direction < 0:
                remaining, cost = units, Decimal("0")
                while remaining > 0 and lots:
                    available, unit_cost = lots[0]
                    used = min(available, remaining)
                    cost += used * unit_cost
                    available -= used
                    remaining -= used
                    if available == 0:
                        lots.popleft()
                    else:
                        lots[0][0] = available
                if remaining > 0:
                    reconciliation_issues.append(
                        f"{scheme['name']}: redemption exceeds reconstructed purchase units by {format(remaining, 'f')}."
                    )
                realized += amount - cost
                all_cashflows.append((tx_date, amount))
            else:
                all_cashflows.append((tx_date, amount))
        units_held = sum((item[0] for item in lots), Decimal("0"))
        if units_held <= 0 and not by_scheme[scheme["id"]]:
            continue
        cost = sum((item[0] * item[1] for item in lots), Decimal("0"))
        nav_row = navs.get(scheme["id"])
        nav = Decimal(nav_row["nav"]) if nav_row else None
        value = units_held * nav if nav is not None else None
        gain = value - cost if value is not None else None
        holdings.append({
            "scheme_id": scheme["id"], "amfi_code": scheme["amfi_code"], "name": scheme["name"],
            "plan": scheme["plan"], "option_name": scheme["option_name"],
            "category": scheme["category"], "units": format(units_held, "f"),
            "cost": format(money(cost), "f"), "nav": format(nav, "f") if nav is not None else None,
            "nav_date": nav_row["nav_date"] if nav_row else None,
            "market_value": format(money(value), "f") if value is not None else None,
            "unrealized_gain": format(money(gain), "f") if gain is not None else None,
            "realized_gain": format(money(realized), "f"),
            "data_status": "current" if nav_row and (valuation - date.fromisoformat(nav_row["nav_date"])).days <= 5 else "missing_or_stale",
        })
    active = [item for item in holdings if Decimal(item["units"]) > 0]
    total_cost = sum((Decimal(item["cost"]) for item in active), Decimal("0"))
    valued = [item for item in holdings if item["market_value"] is not None]
    partial_value = sum((Decimal(item["market_value"]) for item in valued), Decimal("0"))
    missing_active_nav = any(item["market_value"] is None for item in active)
    total_value = None if missing_active_nav else partial_value
    if total_value is not None:
        all_cashflows.append((valuation, total_value))
    rate = xirr(all_cashflows) if total_value is not None and not reconciliation_issues else None
    warnings = list(reconciliation_issues)
    if missing_active_nav:
        warnings.append("Some active holdings have missing NAV data; portfolio totals and XIRR are withheld.")
    elif any(item["data_status"] != "current" for item in active):
        warnings.append("Some holdings use stale NAV data.")
    return {
        "valuation_date": valuation.isoformat(), "calculation_version": "mf-v1",
        "total_cost": format(money(total_cost), "f"),
        "market_value": format(money(total_value), "f") if total_value is not None else None,
        "valued_market_value": format(money(partial_value), "f"),
        "unrealized_gain": format(money(total_value - total_cost), "f") if total_value is not None else None,
        "xirr_percent": round(rate * 100, 2) if rate is not None else None,
        "holdings": holdings,
        "warnings": warnings,
    }


def import_history(path):
    with connect(path) as connection:
        rows = connection.execute("SELECT * FROM mf_import_batches ORDER BY id DESC LIMIT 20").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["report"] = json.loads(item.pop("report_json"))
        result.append(item)
    return result

