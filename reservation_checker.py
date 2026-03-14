#!/usr/bin/env python3
"""
Restaurant Reservation Availability Checker Bot
================================================
Monitors Maido and Central (Lima, Peru) for reservation openings
using the mesa 24/7 API. Sends email notifications when availability is found.

Both restaurants use mesa247.pe for their booking system.
- Maido booking page:   https://maido.mesa247.pe/reservas/maido
- Central booking page: https://central.mesa247.pe/reservas/central

Usage:
    python3 reservation_checker.py

Configuration:
    Edit the CONFIG section below to set your desired dates, party size,
    check interval, and notification preferences.
"""

import json
import logging
import smtplib
import sys
import time
from datetime import datetime, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ==============================================================================
# CONFIGURATION - Edit these values to match your needs
# ==============================================================================

CONFIG = {
    # Date ranges to monitor (inclusive). Add as many as you like.
    "date_ranges": [
        {"start_date": "2026-09-06", "end_date": "2026-09-07"},
        {"start_date": "2026-09-18", "end_date": "2026-09-18"},
    ],

    # Party size (1-6 for online reservations)
    "party_size": 2,

    # How often to check, in seconds (default: every 5 minutes)
    "check_interval_seconds": 300,

    # Restaurants to monitor (comment out any you don't want)
    "restaurants": [
        {
            "name": "Maido",
            "local_id": 2179,
            "booking_url": "https://maido.mesa247.pe/reservas/maido",
        },
        {
            "name": "Central",
            "local_id": 11,
            "booking_url": "https://central.mesa247.pe/reservas/central",
        },
    ],

    # --- Email Notification Settings (optional) ---
    # Set "enabled" to True and fill in the fields to receive email alerts.
    # Example using Gmail: use an App Password (not your regular password).
    # To generate one: Google Account > Security > 2-Step Verification > App passwords
    "email": {
        "enabled": False,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "sender_email": "your_email@gmail.com",
        "sender_password": "your_app_password_here",
        "recipient_email": "your_email@gmail.com",
    },
}

# ==============================================================================
# API Configuration (no need to change these)
# ==============================================================================

MESA247_API_BASE = (
    "https://mesa-backend-prod-370098395535.us-east4.run.app/v2/search"
)

# ==============================================================================
# Logging Setup
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("reservation_checker")

# ==============================================================================
# Core Logic
# ==============================================================================


def check_availability(
    local_id: int, start_date: str, end_date: str, paxs: int
) -> Optional[dict]:
    """
    Query the mesa 24/7 API for date availability.

    Returns the parsed JSON response, or None on error.

    API response structure:
    {
        "time": 1234567890,
        "locals": [{
            "id": <local_id>,
            "name": "<restaurant_name>",
            "dates": [
                {
                    "date": "YYYY-MM-DD",
                    "date_string": "Weekday, Month Day, Year",
                    "available": "0" or "1",   # 1 = slots available
                    "waitlist": "0" or "1",     # 1 = waitlist open
                    "recommendations": "0" or "1",
                    "by": "date" | "day" | "outdate"
                        # "date" = normal bookable day
                        # "day"  = restaurant closed that day
                        # "outdate" = outside the booking window
                },
                ...
            ]
        }]
    }
    """
    params = (
        f"?language=en"
        f"&country_code=PE"
        f"&local_id={local_id}"
        f"&type_reservation=widget"
        f"&start_date={start_date}"
        f"&end_date={end_date}"
        f"&paxs={paxs}"
        f"&from=gateway"
    )
    url = MESA247_API_BASE + params

    try:
        req = Request(url, headers={"User-Agent": "ReservationChecker/1.0"})
        with urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data
    except (URLError, HTTPError, json.JSONDecodeError) as e:
        logger.error(f"API request failed: {e}")
        return None


def filter_available_dates(
    api_response: dict, target_start: str, target_end: str
) -> list[dict]:
    """
    Extract dates that have availability within the target date range.

    Returns a list of dicts with keys: date, date_string, available, waitlist
    """
    available_dates = []

    if not api_response or "locals" not in api_response:
        return available_dates

    start = date.fromisoformat(target_start)
    end = date.fromisoformat(target_end)

    for local in api_response["locals"]:
        for d in local.get("dates", []):
            d_date = date.fromisoformat(d["date"])
            if start <= d_date <= end and d["available"] == "1":
                available_dates.append(
                    {
                        "date": d["date"],
                        "date_string": d["date_string"],
                        "available": d["available"],
                        "waitlist": d["waitlist"],
                    }
                )

    return available_dates


def send_email_notification(
    restaurant_name: str,
    available_dates: list[dict],
    booking_url: str,
    email_config: dict,
) -> bool:
    """Send an email notification about available reservations."""
    if not email_config.get("enabled"):
        return False

    date_list = "\n".join(
        f"  - {d['date_string']} ({d['date']})" for d in available_dates
    )

    subject = f"Reservation Available: {restaurant_name}!"
    body = (
        f"Great news! {restaurant_name} has reservation availability:\n\n"
        f"{date_list}\n\n"
        f"Book now: {booking_url}\n\n"
        f"-- Reservation Checker Bot"
    )

    msg = MIMEMultipart()
    msg["From"] = email_config["sender_email"]
    msg["To"] = email_config["recipient_email"]
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(
            email_config["smtp_server"], email_config["smtp_port"]
        ) as server:
            server.starttls()
            server.login(
                email_config["sender_email"], email_config["sender_password"]
            )
            server.send_message(msg)
        logger.info(f"Email notification sent for {restaurant_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def run_single_check(config: dict) -> dict[str, list[dict]]:
    """
    Run a single check across all configured restaurants and date ranges.

    Returns a dict mapping restaurant name -> list of available dates.
    """
    results = {}
    date_ranges = config["date_ranges"]

    # Compute the outermost start/end so we can fetch in one API call per restaurant
    all_starts = [r["start_date"] for r in date_ranges]
    all_ends = [r["end_date"] for r in date_ranges]
    outer_start = min(all_starts)
    outer_end = max(all_ends)

    for restaurant in config["restaurants"]:
        name = restaurant["name"]
        local_id = restaurant["local_id"]
        booking_url = restaurant["booking_url"]

        logger.info(f"Checking {name} (local_id={local_id})...")

        api_response = check_availability(
            local_id=local_id,
            start_date=outer_start,
            end_date=outer_end,
            paxs=config["party_size"],
        )

        if api_response is None:
            logger.warning(f"Could not fetch data for {name}. Will retry next cycle.")
            continue

        # Collect available dates across all configured date ranges
        available: list[dict] = []
        for dr in date_ranges:
            available.extend(
                filter_available_dates(api_response, dr["start_date"], dr["end_date"])
            )

        if available:
            logger.info(
                f"  AVAILABLE! {name} has {len(available)} date(s) open:"
            )
            for d in available:
                logger.info(f"    -> {d['date_string']} ({d['date']})")

            # Send email notification
            send_email_notification(
                restaurant_name=name,
                available_dates=available,
                booking_url=booking_url,
                email_config=config["email"],
            )
        else:
            logger.info(f"  No availability for {name} in the target ranges.")

        results[name] = available

    return results


def run_once(config: dict) -> None:
    """Run a single check and exit (useful for cron jobs)."""
    logger.info("=" * 60)
    logger.info("Running one-time reservation check")
    for dr in config["date_ranges"]:
        logger.info(f"  Range: {dr['start_date']} to {dr['end_date']}")
    logger.info(f"Party size: {config['party_size']}")
    logger.info("=" * 60)
    run_single_check(config)
    logger.info("Done.")


def run_loop(config: dict) -> None:
    """Run the checker in a continuous loop."""
    interval = config["check_interval_seconds"]
    logger.info("=" * 60)
    logger.info("Starting Reservation Checker Bot (continuous mode)")
    for dr in config["date_ranges"]:
        logger.info(f"  Range: {dr['start_date']} to {dr['end_date']}")
    logger.info(f"Party size: {config['party_size']}")
    logger.info(f"Check interval: {interval} seconds ({interval / 60:.1f} minutes)")
    logger.info(
        f"Monitoring: {', '.join(r['name'] for r in config['restaurants'])}"
    )
    logger.info("=" * 60)

    check_count = 0
    while True:
        check_count += 1
        logger.info(f"--- Check #{check_count} at {datetime.now().isoformat()} ---")

        try:
            run_single_check(config)
        except Exception as e:
            logger.error(f"Unexpected error during check: {e}")

        logger.info(f"Next check in {interval} seconds...")
        time.sleep(interval)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_once(CONFIG)
    else:
        run_loop(CONFIG)


if __name__ == "__main__":
    main()
