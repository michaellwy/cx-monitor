#!/usr/bin/env python3
"""CX Business Class HKG-ICN Availability Dashboard.

Fetches Cathay Pacific business class availability between Hong Kong and Seoul
for the next 6 weeks and generates a self-contained HTML dashboard.
"""

import json
import sys
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

from fli.models import (
    Airline,
    Airport,
    FlightSearchFilters,
    FlightSegment,
    PassengerInfo,
    SeatType,
    TripType,
    MaxStops,
    TimeRestrictions,
)
from fli.search.flights import SearchFlights

WEEKS_AHEAD = 6
OUTPUT_FILE = Path(__file__).parent / "cx_dashboard.html"
REQUEST_DELAY = 2  # seconds between API calls to avoid 429s

search_client = SearchFlights(currency="HKD", country="hk")


def get_target_weeks(weeks_ahead=WEEKS_AHEAD):
    """Compute the next N weeks of Mon/Sat/Sun dates."""
    today = datetime.now().date()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0 and datetime.now().hour >= 18:
        days_until_monday = 7
    next_monday = today + timedelta(days=days_until_monday)

    weeks = []
    for i in range(weeks_ahead):
        monday = next_monday + timedelta(weeks=i)
        saturday = monday + timedelta(days=5)
        sunday = monday + timedelta(days=6)

        if i == 0:
            proximity = "this-week"
        elif i == 1:
            proximity = "next-week"
        else:
            proximity = ""

        weeks.append({
            "monday": monday.isoformat(),
            "saturday": saturday.isoformat(),
            "sunday": sunday.isoformat(),
            "monday_display": monday.strftime("%a, %b %d"),
            "saturday_display": saturday.strftime("%a, %b %d"),
            "sunday_display": sunday.strftime("%a, %b %d"),
            "week_label": f"Week of {monday.strftime('%b %d')}",
            "proximity": proximity,
        })
    return weeks


def _search_with_retry(filters, retries=4):
    """Search with retry and delay for rate limiting."""
    for attempt in range(retries):
        try:
            time.sleep(REQUEST_DELAY)
            return search_client.search(filters)
        except Exception as e:
            if "429" in str(e) and attempt < retries - 1:
                wait = 15 * (attempt + 1)
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    return None


def search_flights(origin, destination, date, afternoon_only=False):
    """Search for CX business class non-stop flights on a given date."""
    time_filter = TimeRestrictions(earliest_departure=12) if afternoon_only else None
    segment = FlightSegment(
        departure_airport=[[origin, 0]],
        arrival_airport=[[destination, 0]],
        travel_date=date,
        time_restrictions=time_filter,
    )
    filters = FlightSearchFilters(
        trip_type=TripType.ONE_WAY,
        passenger_info=PassengerInfo(adults=1),
        flight_segments=[segment],
        seat_type=SeatType.BUSINESS,
        airlines=[Airline.CX],
        stops=MaxStops.NON_STOP,
    )
    return _search_with_retry(filters)


def search_economy_check(origin, destination, date):
    """Check if CX flies this route at all (economy) for sold-out detection."""
    segment = FlightSegment(
        departure_airport=[[origin, 0]],
        arrival_airport=[[destination, 0]],
        travel_date=date,
    )
    filters = FlightSearchFilters(
        trip_type=TripType.ONE_WAY,
        passenger_info=PassengerInfo(adults=1),
        flight_segments=[segment],
        seat_type=SeatType.ECONOMY,
        airlines=[Airline.CX],
        stops=MaxStops.NON_STOP,
    )
    return _search_with_retry(filters)


CATHAY_BOOK_URL = "https://www.cathaypacific.com/cx/en_HK/book-a-trip.html"


def make_book_url(origin_code, dest_code):
    """Construct Cathay Pacific booking search URL."""
    return CATHAY_BOOK_URL


def parse_flights(results, date, origin_code, dest_code):
    """Parse FlightResult list into simple dicts."""
    if not results:
        return []
    flights = []
    for f in results:
        leg = f.legs[0]
        flights.append({
            "flight_number": f"CX {leg.flight_number}",
            "departure": leg.departure_datetime.strftime("%H:%M"),
            "arrival": leg.arrival_datetime.strftime("%H:%M"),
            "duration_min": f.duration,
            "duration_str": f"{f.duration // 60}h {f.duration % 60:02d}m",
            "price_hkd": int(f.price),
            "aircraft": leg.aircraft,
            "book_url": CATHAY_BOOK_URL,
        })
    return sorted(flights, key=lambda x: x["departure"])


def parse_economy_flights(results):
    """Parse economy results into flight time dicts (no prices)."""
    if not results:
        return []
    flights = []
    for f in results:
        leg = f.legs[0]
        flights.append({
            "flight_number": f"CX {leg.flight_number}",
            "departure": leg.departure_datetime.strftime("%H:%M"),
            "arrival": leg.arrival_datetime.strftime("%H:%M"),
            "duration_str": f"{f.duration // 60}h {f.duration % 60:02d}m",
            "aircraft": leg.aircraft,
        })
    return sorted(flights, key=lambda x: x["departure"])


def fetch_day(origin, destination, date, afternoon_only=False):
    """Fetch availability for a single day, with sold-out cross-check."""
    origin_code = origin.name
    dest_code = destination.name
    results = search_flights(origin, destination, date, afternoon_only)
    flights = parse_flights(results, date, origin_code, dest_code)
    book_url = make_book_url(origin_code, dest_code)

    if flights:
        count = len(flights)
        return {
            "date": date,
            "status": "available" if count >= 3 else "limited",
            "flights": flights,
            "cheapest_hkd": min(f["price_hkd"] for f in flights),
            "count": count,
            "sold_out_flights": [],
            "book_url": book_url,
        }

    # No business class results — check if CX economy exists
    econ_results = search_economy_check(origin, destination, date)
    if econ_results and len(econ_results) > 0:
        status = "sold_out"
        sold_out_flights = parse_economy_flights(econ_results)
    else:
        status = "no_service"
        sold_out_flights = []

    return {
        "date": date,
        "status": status,
        "flights": [],
        "cheapest_hkd": None,
        "count": 0,
        "sold_out_flights": sold_out_flights,
        "book_url": book_url,
    }


def fetch_all(weeks):
    """Fetch availability for all weeks."""
    data = []
    for i, week in enumerate(weeks):
        print(f"  [{i+1}/{len(weeks)}] {week['week_label']}...")
        mon = fetch_day(Airport.HKG, Airport.ICN, week["monday"], afternoon_only=True)
        sat = fetch_day(Airport.ICN, Airport.HKG, week["saturday"])
        sun = fetch_day(Airport.ICN, Airport.HKG, week["sunday"])

        statuses = [mon["status"], sat["status"], sun["status"]]
        if mon["status"] in ("sold_out", "no_service"):
            week_status = "critical"
        elif "sold_out" in statuses or "limited" in statuses:
            week_status = "warning"
        else:
            week_status = "good"

        data.append({
            **week,
            "week_status": week_status,
            "outbound": mon,
            "return_sat": sat,
            "return_sun": sun,
        })
    return data


# --- KRW exchange rate (approximate) ---
HKD_TO_KRW = 178  # ~1 HKD = 178 KRW


def generate_html(weeks_data, timestamp):
    """Generate self-contained mobile-first HTML dashboard."""

    def fmt_aircraft(ac):
        """Format aircraft string into short label + optional Aria badge."""
        if not ac:
            return ""
        short = ac.replace("Boeing ", "").replace("Airbus ", "")
        # Aria Suite is only on 777-300ER
        aria = ' <span class="aria-badge" title="May feature the Cathay Aria Suite">Aria Suite</span>' if "777-300ER" in ac else ""
        return f'<span class="fl-aircraft">{short}{aria}</span>'

    def build_flight_rows(flights):
        rows = ""
        for fl in flights:
            price_hkd = fl["price_hkd"]
            price_krw = int(price_hkd * HKD_TO_KRW)
            ac_html = fmt_aircraft(fl.get("aircraft"))
            rows += f'''<div class="flight-row">
  <div class="fl-main">
    <span class="fl-num">{fl['flight_number']}</span>
    <span class="fl-time">{fl['departure']}<span class="fl-arrow">&rarr;</span>{fl['arrival']}</span>
    <span class="fl-dur">{fl['duration_str']}</span>
    <span class="fl-price" data-hkd="{price_hkd}" data-krw="{price_krw}">
      <span class="price-hkd">HK${price_hkd:,}</span>
      <span class="price-krw" style="display:none">&#8361;{price_krw:,}</span>
    </span>
  </div>
  {f'<div class="fl-meta">{ac_html}</div>' if ac_html else ''}
</div>\n'''
        return rows

    def build_sold_out_rows(sold_out_flights):
        if not sold_out_flights:
            return ""
        rows = ""
        for fl in sold_out_flights:
            ac_html = fmt_aircraft(fl.get("aircraft"))
            rows += f'''<div class="flight-row so-flight">
  <div class="fl-main">
    <span class="fl-num">{fl['flight_number']}</span>
    <span class="fl-time">{fl['departure']}<span class="fl-arrow">&rarr;</span>{fl['arrival']}</span>
    <span class="fl-dur">{fl['duration_str']}</span>
    <span class="fl-sold-tag">Sold out</span>
  </div>
  {f'<div class="fl-meta">{ac_html}</div>' if ac_html else ''}
</div>\n'''
        return f'<div class="sold-out-flights">{rows}</div>'

    def build_day_section(day_data, heading, date_display):
        status = day_data["status"]
        count = day_data["count"]

        status_map = {
            "available": ("Available", "st-available"),
            "limited": ("Limited", "st-limited"),
            "sold_out": ("Sold Out", "st-sold-out"),
            "no_service": ("No Service", "st-no-service"),
        }
        label, cls = status_map.get(status, ("Unknown", ""))

        count_text = ""
        if count > 0:
            count_text = f"{count} flight{'s' if count != 1 else ''}"

        price_html = ""
        if day_data["cheapest_hkd"]:
            hkd = day_data["cheapest_hkd"]
            krw = int(hkd * HKD_TO_KRW)
            price_html = f'''<span class="sec-price" data-hkd="{hkd}" data-krw="{krw}">
  <span class="price-hkd">from HK${hkd:,}</span>
  <span class="price-krw" style="display:none">from &#8361;{krw:,}</span>
</span>'''
        elif status == "sold_out":
            price_html = '<span class="sec-note">Business class fully booked</span>'

        flights_html = build_flight_rows(day_data["flights"]) if day_data["flights"] else ""
        sold_out_html = build_sold_out_rows(day_data.get("sold_out_flights", [])) if status == "sold_out" else ""

        cx_url = CATHAY_BOOK_URL
        book_btn = ""
        if status in ("available", "limited"):
            book_btn = f'<a href="{cx_url}" target="_blank" rel="noopener" class="sec-book-btn">Book on Cathay &rarr;</a>'
        elif status == "sold_out":
            book_btn = f'<a href="{cx_url}" target="_blank" rel="noopener" class="sec-book-btn sec-book-waitlist">Check availability &rarr;</a>'

        count_price = ""
        if count_text and price_html:
            count_price = f'{count_text} &middot; {price_html}'
        elif count_text:
            count_price = count_text
        elif price_html:
            count_price = price_html

        return f'''<div class="day-sec {cls}">
  <div class="sec-head">
    <div class="sec-left">
      <span class="sec-date">{date_display}</span>
      <span class="sec-direction">{heading}</span>
    </div>
    <span class="sec-status {cls}">{label}</span>
  </div>
  {f'<div class="sec-meta">{count_price}</div>' if count_price else ''}
  {f'<div class="flights">{flights_html}</div>' if flights_html else ''}
  {sold_out_html}
  {book_btn}
</div>\n'''

    # Build week cards
    cards_html = ""
    for week in weeks_data:
        prox_badge = ""
        if week["proximity"] == "this-week":
            prox_badge = '<span class="prox-badge this">This Week</span>'
        elif week["proximity"] == "next-week":
            prox_badge = '<span class="prox-badge next">Next Week</span>'

        ws_cls = {"good": "ws-good", "warning": "ws-warn", "critical": "ws-crit"}.get(week["week_status"], "")

        # Quick status summary for collapsed view
        statuses = [week["outbound"]["status"], week["return_sat"]["status"], week["return_sun"]["status"]]
        summary_parts = []
        if "sold_out" in statuses:
            sold_count = statuses.count("sold_out")
            summary_parts.append(f'<span class="sum-bad">{sold_count} sold out</span>')
        if "limited" in statuses:
            lim_count = statuses.count("limited")
            summary_parts.append(f'<span class="sum-warn">{lim_count} limited</span>')
        avail_count = statuses.count("available")
        if avail_count > 0:
            summary_parts.append(f'<span class="sum-ok">{avail_count} available</span>')
        summary_html = " &middot; ".join(summary_parts)

        outbound = build_day_section(week["outbound"], "Outbound to Seoul", week["monday_display"])
        ret_sat = build_day_section(week["return_sat"], "Return to Hong Kong", week["saturday_display"])
        ret_sun = build_day_section(week["return_sun"], "Return to Hong Kong", week["sunday_display"])

        cards_html += f'''<div class="week-card {ws_cls}">
  <div class="card-head" onclick="toggleCard(this)">
    <div class="card-head-left">
      <span class="card-title">{week['week_label']}</span>
      <span class="card-summary">{summary_html}</span>
    </div>
    <div class="card-head-right">
      {prox_badge}
      <span class="chevron">&#9662;</span>
    </div>
  </div>
  <div class="card-body">
    {outbound}
    <div class="return-group">
      <div class="return-label">Return options</div>
      {ret_sat}
      {ret_sun}
    </div>
  </div>
</div>\n'''

    ts = timestamp.strftime("%b %d, %Y at %H:%M HKT")

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="apple-itunes-app" content="app-id=305038764">
<title>Cathay Business — HKG / ICN Weekly</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=Fraunces:opsz,wght@9..144,400;9..144,600&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{ margin:0;padding:0;box-sizing:border-box; }}
:root {{
  --bg: #0a0e14;
  --card: #12171f;
  --card-border: #1c2333;
  --text-1: #f0f2f5;
  --text-2: #9ca3af;
  --text-3: #5c6370;
  --jade: #006564;
  --jade-light: #00a89d;
  --jade-glow: #00a89d22;
  --green: #3dd68c;
  --green-bg: #3dd68c12;
  --amber: #f5a623;
  --amber-bg: #f5a62312;
  --red: #e5484d;
  --red-bg: #e5484d10;
  --radius: 14px;
  --font-display: 'Fraunces', Georgia, serif;
  --font-body: 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif;
}}
html {{ font-size: 15px; }}
body {{
  font-family: var(--font-body);
  background: var(--bg);
  color: var(--text-1);
  min-height: 100dvh;
  -webkit-font-smoothing: antialiased;
}}

/* ── Header ────────────────────────────── */
header {{
  position: sticky; top: 0; z-index: 20;
  background: var(--bg);
  border-bottom: 1px solid var(--card-border);
  padding: 16px 20px;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}}
.header-row {{
  display: flex; align-items: center; justify-content: space-between;
  max-width: 720px; margin: 0 auto;
}}
.brand {{
  display: flex; align-items: baseline; gap: 8px;
}}
.brand-mark {{
  font-family: var(--font-display);
  font-size: 1.35rem; font-weight: 600;
  color: var(--jade-light);
  letter-spacing: -0.02em;
}}
.brand-route {{
  font-size: 0.8rem; color: var(--text-3);
  font-weight: 500; letter-spacing: 0.04em; text-transform: uppercase;
}}
.header-right {{
  display: flex; align-items: center; gap: 12px;
}}
.currency-toggle {{
  display: flex;
  background: var(--card);
  border: 1px solid var(--card-border);
  border-radius: 8px;
  overflow: hidden;
  font-size: 0.73rem;
  font-weight: 600;
}}
.curr-btn {{
  padding: 5px 10px;
  cursor: pointer;
  color: var(--text-3);
  transition: all 0.2s;
  border: none; background: none;
  font-family: inherit; font-size: inherit; font-weight: inherit;
}}
.curr-btn.active {{
  background: var(--jade);
  color: #fff;
}}
.timestamp {{
  font-size: 0.7rem; color: var(--text-3);
  display: none;
}}

/* ── Cards container ───────────────────── */
.cards {{
  padding: 16px;
  display: flex; flex-direction: column; gap: 14px;
  max-width: 720px; margin: 0 auto;
  padding-bottom: env(safe-area-inset-bottom, 20px);
}}

/* ── Week card ─────────────────────────── */
.week-card {{
  background: var(--card);
  border: 1px solid var(--card-border);
  border-radius: var(--radius);
  overflow: hidden;
  transition: border-color 0.2s;
}}
.week-card.ws-crit {{ border-color: #e5484d40; }}
.week-card.ws-warn {{ border-color: #f5a62330; }}
.card-head {{
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 16px 12px;
  border-bottom: 1px solid var(--card-border);
  cursor: pointer;
  user-select: none;
  -webkit-user-select: none;
  transition: background 0.15s;
}}
.card-head:hover {{ background: #ffffff06; }}
.card-head-left {{ display: flex; flex-direction: column; gap: 3px; }}
.card-head-right {{ display: flex; align-items: center; gap: 8px; flex-shrink: 0; }}
.card-title {{
  font-family: var(--font-display);
  font-size: 1.05rem; font-weight: 600;
  color: var(--text-1);
}}
.card-summary {{
  font-size: 0.7rem; color: var(--text-3);
  display: none;
}}
.week-card.collapsed .card-summary {{ display: block; }}
.sum-bad {{ color: var(--red); }}
.sum-warn {{ color: var(--amber); }}
.sum-ok {{ color: var(--green); }}
.chevron {{
  font-size: 0.7rem; color: var(--text-3);
  transition: transform 0.2s;
}}
.week-card.collapsed .chevron {{ transform: rotate(-90deg); }}
.card-body {{
  transition: max-height 0.3s ease, opacity 0.2s ease;
  overflow: hidden;
}}
.week-card.collapsed .card-body {{
  display: none;
}}
.week-card.collapsed .card-head {{
  border-bottom: none;
}}
.prox-badge {{
  font-size: 0.65rem; font-weight: 700;
  padding: 3px 8px; border-radius: 6px;
  text-transform: uppercase; letter-spacing: 0.06em;
}}
.prox-badge.this {{
  background: var(--jade-glow);
  color: var(--jade-light);
  border: 1px solid var(--jade-light);
}}
.prox-badge.next {{
  background: transparent;
  color: var(--text-3);
  border: 1px solid var(--card-border);
}}

/* ── Day section ───────────────────────── */
.day-sec {{
  padding: 14px 16px;
  border-bottom: 1px solid var(--card-border);
}}
.day-sec:last-child {{ border-bottom: none; }}
.day-sec.st-available, .day-sec.st-limited {{ }}
.day-sec.st-sold-out {{ background: var(--red-bg); }}
.day-sec.st-no-service {{ opacity: 0.5; }}

.sec-head {{
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px; margin-bottom: 2px;
}}
.sec-left {{ display: flex; flex-direction: column; gap: 1px; }}
.sec-date {{
  font-size: 0.9rem; font-weight: 600; color: var(--text-1);
  letter-spacing: -0.01em;
}}
.sec-direction {{
  font-size: 0.68rem; color: var(--text-3);
  font-weight: 400; text-transform: uppercase; letter-spacing: 0.04em;
}}
.sec-status {{
  font-size: 0.65rem; font-weight: 700; padding: 2px 8px;
  border-radius: 5px; letter-spacing: 0.04em; text-transform: uppercase;
  flex-shrink: 0;
}}
.sec-status.st-available {{ background: var(--green-bg); color: var(--green); }}
.sec-status.st-limited {{ background: var(--amber-bg); color: var(--amber); }}
.sec-status.st-sold-out {{ background: var(--red-bg); color: var(--red); }}
.sec-status.st-no-service {{ background: var(--card-border); color: var(--text-3); }}
.sec-meta {{
  font-size: 0.73rem; color: var(--text-3);
  margin-bottom: 2px;
}}
.sec-price {{
  font-size: 0.73rem; font-weight: 600;
  color: var(--jade-light);
}}
.sec-note {{
  font-size: 0.73rem; color: var(--text-3);
  font-style: italic;
}}

/* ── Return group ──────────────────────── */
.return-group {{
  border-top: 1px solid var(--card-border);
}}
.return-label {{
  font-size: 0.68rem; font-weight: 600; color: var(--text-3);
  text-transform: uppercase; letter-spacing: 0.08em;
  padding: 10px 16px 0;
}}

/* ── Flight rows ───────────────────────── */
.flights {{ margin-top: 8px; display: flex; flex-direction: column; gap: 2px; }}
.flight-row {{
  display: flex; flex-direction: column;
  padding: 8px 10px; border-radius: 8px;
  font-size: 0.8rem;
}}
.fl-main {{
  display: flex; align-items: center; gap: 6px;
}}
.fl-meta {{
  display: flex; align-items: center; gap: 6px;
  padding-left: 0; margin-top: 3px;
}}
.fl-num {{
  font-weight: 600; color: var(--text-2);
  min-width: 52px;
}}
.fl-time {{
  color: var(--text-1); font-weight: 500;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}}
.fl-arrow {{ color: var(--text-3); margin: 0 3px; font-size: 0.7rem; }}
.fl-dur {{ color: var(--text-3); font-size: 0.73rem; white-space: nowrap; }}
.fl-price {{
  margin-left: auto;
  font-weight: 600; color: var(--jade-light);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}}
/* ── Aircraft info ────────────────────── */
.fl-aircraft {{
  font-size: 0.68rem; color: var(--text-3);
  font-weight: 400;
}}
.aria-badge {{
  display: inline-block;
  font-size: 0.6rem; font-weight: 700;
  color: #c9a84c;
  background: #c9a84c14;
  border: 1px solid #c9a84c40;
  padding: 1px 6px; border-radius: 4px;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  vertical-align: middle;
  margin-left: 4px;
}}

/* ── Controls row ─────────────────────── */
.controls-row {{
  margin-top: 8px;
  gap: 8px;
}}
.toggle-group {{
  display: flex; gap: 16px; align-items: center;
}}

/* ── Toggle switch ────────────────────── */
.sw-label {{
  display: flex; align-items: center; gap: 8px;
  cursor: pointer; user-select: none; -webkit-user-select: none;
}}
.sw-label input {{ display: none; }}
.sw-track {{
  position: relative;
  width: 34px; height: 20px;
  background: var(--card-border);
  border-radius: 10px;
  transition: background 0.25s;
  flex-shrink: 0;
}}
.sw-thumb {{
  position: absolute;
  top: 2px; left: 2px;
  width: 16px; height: 16px;
  background: var(--text-3);
  border-radius: 50%;
  transition: transform 0.25s, background 0.25s;
}}
.sw-label input:checked + .sw-track {{
  background: var(--jade);
}}
.sw-label input:checked + .sw-track .sw-thumb {{
  transform: translateX(14px);
  background: #fff;
}}
.sw-text {{
  font-size: 0.73rem; color: var(--text-2);
  font-weight: 500;
}}
.collapse-all-btn {{
  font-family: inherit;
  font-size: 0.7rem; font-weight: 500;
  color: var(--text-3);
  background: var(--card);
  border: 1px solid var(--card-border);
  border-radius: 6px;
  padding: 4px 10px;
  cursor: pointer;
  transition: all 0.15s;
}}
.collapse-all-btn:hover {{
  color: var(--text-2);
  border-color: var(--text-3);
}}

/* ── Price hidden mode ────────────────── */
body.prices-hidden .fl-price,
body.prices-hidden .sec-price {{
  display: none !important;
}}

/* ── Sold-out flight rows ─────────────── */
.sold-out-flights {{
  margin-top: 8px;
  display: none;
}}
body.show-soldout .sold-out-flights {{
  display: flex;
  flex-direction: column;
  gap: 2px;
}}
.so-flight {{
  opacity: 0.55;
}}
.fl-sold-tag {{
  margin-left: auto;
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--red);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}}

/* ── Section book button ──────────────── */
.sec-book-btn {{
  display: inline-block;
  margin-top: 8px;
  font-size: 0.73rem; font-weight: 600;
  color: var(--jade-light);
  text-decoration: none;
  padding: 6px 12px;
  border: 1px solid var(--jade);
  border-radius: 8px;
  transition: all 0.15s;
}}
.sec-book-btn:hover {{
  background: var(--jade-glow);
}}
.sec-book-waitlist {{
  color: var(--text-3);
  border-color: var(--card-border);
}}
.sec-book-waitlist:hover {{
  color: var(--text-2);
  background: #ffffff08;
}}

/* ── Refresh card (inside .cards grid) ── */
.refresh-card {{
  background: var(--card);
  border: 1px solid var(--card-border);
  border-radius: var(--radius);
  padding: 14px 16px;
  grid-column: 1 / -1;
}}
.refresh-top {{
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 10px;
}}
.refresh-text {{
  font-size: 0.73rem; color: var(--text-2);
}}
.refresh-text strong {{
  color: var(--text-1); font-weight: 600;
}}
.refresh-btn {{
  font-family: inherit;
  font-size: 0.73rem; font-weight: 600;
  color: var(--jade-light);
  background: none;
  border: 1px solid var(--jade);
  border-radius: 8px;
  padding: 5px 14px;
  cursor: pointer;
  text-decoration: none;
  transition: background 0.15s;
}}
.refresh-btn:hover {{
  background: var(--jade-glow);
}}
.refresh-footer {{
  font-size: 0.68rem; color: var(--text-3);
  line-height: 1.6;
  border-top: 1px solid var(--card-border);
  padding-top: 10px;
}}
.refresh-footer a {{ color: var(--text-2); text-decoration: none; }}
.refresh-footer a:hover {{ text-decoration: underline; }}

/* ── Desktop ───────────────────────────── */
@media (min-width: 768px) {{
  .cards {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
    max-width: 960px;
    padding: 24px;
  }}
  .week-card {{ align-self: start; }}
  .header-row {{ max-width: 960px; }}
  .timestamp {{ display: block; }}
  header {{ padding: 18px 24px; }}
}}
</style>
</head>
<body>
<header>
  <div class="header-row">
    <div class="brand">
      <span class="brand-mark">Cathay Business</span>
      <span class="brand-route">Hong Kong &mdash; Seoul</span>
    </div>
    <div class="header-right">
      <div class="currency-toggle">
        <button class="curr-btn active" data-curr="hkd" onclick="setCurrency('hkd')">HKD</button>
        <button class="curr-btn" data-curr="krw" onclick="setCurrency('krw')">KRW</button>
      </div>
      <!-- timestamp moved to refresh bar -->
    </div>
  </div>
  <div class="header-row controls-row">
    <div class="toggle-group">
      <label class="sw-label">
        <input type="checkbox" id="toggle-prices" checked onchange="togglePrices(this.checked)">
        <span class="sw-track"><span class="sw-thumb"></span></span>
        <span class="sw-text">Prices</span>
      </label>
      <label class="sw-label">
        <input type="checkbox" id="toggle-soldout" onchange="toggleSoldOutFlights(this.checked)">
        <span class="sw-track"><span class="sw-thumb"></span></span>
        <span class="sw-text">Sold-out flights</span>
      </label>
    </div>
    <button class="collapse-all-btn" onclick="collapseAll()">Collapse all</button>
  </div>
</header>

<div class="cards">
{cards_html}
<div class="refresh-card">
  <div class="refresh-top">
    <span class="refresh-text">Updated <strong>{ts}</strong></span>
    <a href="javascript:location.reload()" class="refresh-btn">Refresh</a>
  </div>
  <div class="refresh-footer">
    Cathay Pacific Business Class &middot; Non-stop only &middot; Prices are indicative<br>
    <a href="{CATHAY_BOOK_URL}" target="_blank" rel="noopener">Book on cathaypacific.com</a>
  </div>
</div>
</div>

<script>
function setCurrency(curr) {{
  document.querySelectorAll('.curr-btn').forEach(b => {{
    b.classList.toggle('active', b.dataset.curr === curr);
  }});
  document.querySelectorAll('.price-hkd').forEach(el => {{
    el.style.display = curr === 'hkd' ? '' : 'none';
  }});
  document.querySelectorAll('.price-krw').forEach(el => {{
    el.style.display = curr === 'krw' ? '' : 'none';
  }});
}}

function toggleCard(headEl) {{
  headEl.closest('.week-card').classList.toggle('collapsed');
}}

function collapseAll() {{
  const cards = document.querySelectorAll('.week-card');
  const allCollapsed = [...cards].every(c => c.classList.contains('collapsed'));
  cards.forEach(c => {{
    if (allCollapsed) c.classList.remove('collapsed');
    else c.classList.add('collapsed');
  }});
  document.querySelector('.collapse-all-btn').textContent = allCollapsed ? 'Collapse all' : 'Expand all';
}}

function togglePrices(show) {{
  document.body.classList.toggle('prices-hidden', !show);
}}

function toggleSoldOutFlights(show) {{
  document.body.classList.toggle('show-soldout', show);
}}
</script>
</body>
</html>'''
    return html


def generate_sample_data(weeks):
    """Generate realistic sample data for UI testing."""
    import random
    random.seed(42)

    bk = CATHAY_BOOK_URL

    sample_flights_out = [
        {"flight_number": "CX 418", "departure": "14:30", "arrival": "19:15", "duration_min": 225,
         "duration_str": "3h 45m", "price_hkd": 8490, "aircraft": "Boeing 777-300ER", "book_url": bk},
        {"flight_number": "CX 420", "departure": "16:00", "arrival": "20:40", "duration_min": 220,
         "duration_str": "3h 40m", "price_hkd": 9120, "aircraft": "Airbus A350-900", "book_url": bk},
        {"flight_number": "CX 422", "departure": "18:30", "arrival": "23:10", "duration_min": 220,
         "duration_str": "3h 40m", "price_hkd": 7800, "aircraft": "Airbus A330-300", "book_url": bk},
    ]
    sample_flights_ret = [
        {"flight_number": "CX 419", "departure": "09:00", "arrival": "11:50", "duration_min": 230,
         "duration_str": "3h 50m", "price_hkd": 7200, "aircraft": "Boeing 777-300ER", "book_url": bk},
        {"flight_number": "CX 421", "departure": "13:30", "arrival": "16:20", "duration_min": 230,
         "duration_str": "3h 50m", "price_hkd": 8100, "aircraft": "Airbus A350-900", "book_url": bk},
        {"flight_number": "CX 415", "departure": "20:00", "arrival": "22:50", "duration_min": 230,
         "duration_str": "3h 50m", "price_hkd": 6900, "aircraft": "Boeing 777-300ER", "book_url": bk},
    ]

    # Economy flight times shown when business is sold out
    sold_out_out = [
        {"flight_number": "CX 418", "departure": "14:30", "arrival": "19:15", "duration_str": "3h 45m", "aircraft": "Boeing 777-300ER"},
        {"flight_number": "CX 420", "departure": "16:00", "arrival": "20:40", "duration_str": "3h 40m", "aircraft": "Airbus A350-900"},
    ]
    sold_out_ret = [
        {"flight_number": "CX 419", "departure": "09:00", "arrival": "11:50", "duration_str": "3h 50m", "aircraft": "Boeing 777-300ER"},
        {"flight_number": "CX 421", "departure": "13:30", "arrival": "16:20", "duration_str": "3h 50m", "aircraft": "Airbus A350-900"},
    ]

    scenarios = [
        ("available", "available", "available"),
        ("available", "limited", "sold_out"),
        ("limited", "available", "available"),
        ("sold_out", "available", "limited"),
        ("available", "available", "available"),
        ("available", "sold_out", "available"),
    ]

    data = []
    for i, week in enumerate(weeks):
        out_s, sat_s, sun_s = scenarios[i % len(scenarios)]

        def make_day(status, flights_pool, so_pool):
            if status == "available":
                flights = sorted(random.sample(flights_pool, k=3), key=lambda x: x["departure"])
                return {"date": "", "status": "available", "flights": flights, "cheapest_hkd": min(f["price_hkd"] for f in flights), "count": 3, "sold_out_flights": [], "book_url": bk}
            elif status == "limited":
                flights = sorted(random.sample(flights_pool, k=1), key=lambda x: x["departure"])
                return {"date": "", "status": "limited", "flights": flights, "cheapest_hkd": flights[0]["price_hkd"], "count": 1, "sold_out_flights": [], "book_url": bk}
            else:
                return {"date": "", "status": "sold_out", "flights": [], "cheapest_hkd": None, "count": 0, "sold_out_flights": so_pool, "book_url": bk}

        mon = make_day(out_s, sample_flights_out, sold_out_out)
        sat = make_day(sat_s, sample_flights_ret, sold_out_ret)
        sun = make_day(sun_s, sample_flights_ret, sold_out_ret)

        statuses = [mon["status"], sat["status"], sun["status"]]
        if mon["status"] in ("sold_out", "no_service"):
            ws = "critical"
        elif "sold_out" in statuses or "limited" in statuses:
            ws = "warning"
        else:
            ws = "good"

        data.append({**week, "week_status": ws, "outbound": mon, "return_sat": sat, "return_sun": sun})
    return data


def main():
    use_sample = "--sample" in sys.argv

    print("Cathay Business HKG-ICN Monitor")
    print("=" * 40)

    weeks = get_target_weeks()

    if use_sample:
        print("Using sample data for preview...\n")
        data = generate_sample_data(weeks)
    else:
        print(f"Fetching availability for next {WEEKS_AHEAD} weeks...\n")
        data = fetch_all(weeks)

    print("\nGenerating dashboard...")
    html = generate_html(data, datetime.now())
    OUTPUT_FILE.write_text(html)
    print(f"Written to {OUTPUT_FILE}")

    print("\nSummary:")
    for week in data:
        icon = {"good": "✓", "warning": "◐", "critical": "✗"}[week["week_status"]]
        print(f"  {icon} {week['week_label']}: Out={week['outbound']['status']}, Sat={week['return_sat']['status']}, Sun={week['return_sun']['status']}")

    webbrowser.open(OUTPUT_FILE.resolve().as_uri())


if __name__ == "__main__":
    main()
