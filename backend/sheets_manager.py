"""
Google Sheets manager for CozyBrew Coffee Shop
"""

import json
import os
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsManager:
    def __init__(self):
        self.sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
        self.credentials_env = os.getenv("GOOGLE_CREDENTIALS", "credentials.json")
        self._service = None
        self._initialized = False
        self._init_error = None
        self._initialize()

    def _initialize(self):
        try:
            if not self.sheet_id:
                self._init_error = "GOOGLE_SHEET_ID not set in .env"
                print(f"[SheetsManager] {self._init_error}")
                return

            creds = None
            # 1. Try loading directly from JSON string passed as environment variable
            if self.credentials_env and self.credentials_env.strip().startswith("{"):
                try:
                    creds_info = json.loads(self.credentials_env)
                    creds = Credentials.from_service_account_info(
                        creds_info, scopes=SCOPES
                    )
                except Exception as json_err:
                    print(f"[SheetsManager] Failed to parse JSON from env: {json_err}")

            # 2. Fallback to reading local credentials file (for local development)
            if creds is None:
                creds_file = self.credentials_env or "credentials.json"
                if not os.path.exists(creds_file):
                    self._init_error = f"Credentials file or valid JSON env variable not found: {creds_file}"
                    print(f"[SheetsManager] {self._init_error}")
                    return

                creds = Credentials.from_service_account_file(
                    creds_file, scopes=SCOPES
                )

            self._service = build("sheets", "v4", credentials=creds)
            self._initialized = True
            print("[SheetsManager] Initialized successfully")

        except Exception as e:
            self._init_error = str(e)
            print(f"[SheetsManager] Init failed: {e}")

    @property
    def is_available(self):
        return self._initialized and self._service is not None

    def _get_all_records_safe(self, tab_name):
        if not self.is_available:
            return []

        try:
            result = (
                self._service.spreadsheets()
                .values()
                .get(spreadsheetId=self.sheet_id, range=tab_name)
                .execute()
            )

            values = result.get("values", [])
            if not values or len(values) < 2:
                return []

            headers = values[0]
            records = []
            for row in values[1:]:
                record = {}
                for i, header in enumerate(headers):
                    record[header] = row[i] if i < len(row) else ""
                records.append(record)

            return records

        except Exception as e:
            print(f"[SheetsManager] Error reading '{tab_name}': {e}")
            return []

    def _append_row_safe(self, tab_name, row):
        if not self.is_available:
            print(f"[SheetsManager] Sheets not available, cannot append to '{tab_name}'")
            return False

        try:
            self._service.spreadsheets().values().append(
                spreadsheetId=self.sheet_id,
                range=f"{tab_name}!A1",
                valueInputOption="RAW",
                body={"values": [row]},
            ).execute()
            return True
        except Exception as e:
            print(f"[SheetsManager] Failed to append to '{tab_name}': {e}")
            return False

    def get_faqs(self):
        records = self._get_all_records_safe("FAQs")
        faqs = []
        for r in records:
            faqs.append({
                "question": r.get("Question", ""),
                "answer": r.get("Answer", ""),
                "category": r.get("Category", "general"),
            })
        return faqs

    def get_menu(self):
        records = self._get_all_records_safe("Menu")
        menu = {"coffee": [], "food": [], "other": []}
        for r in records:
            name = r.get("Item Name", "")
            price = r.get("Price", "")
            category = r.get("Category", "other").lower()
            available = r.get("Available", "true").lower() in ("true", "yes", "1")
            item = {"name": name, "price": price, "available": available}

            if "coffee" in category or "drink" in category or "beverage" in category:
                menu["coffee"].append(item)
            elif "food" in category or "snack" in category or "bakery" in category:
                menu["food"].append(item)
            else:
                menu["other"].append(item)
        return menu

    def get_knowledge_base(self):
        return {
            "faqs": self.get_faqs(),
            "menu": self.get_menu(),
            "company": {
                "name": "CozyBrew Coffee Shop",
                "address": "123 Coffee Lane, Downtown, Texas 75001",
                "phone": "+1-585-0131",
                "email": "hello@cozybrew.gmail.com",
                "hours": {
                    "mon_fri": "7:00 AM - 8:00 PM",
                    "saturday": "8:00 AM - 9:00 PM",
                    "sunday": "8:00 AM - 7:00 PM",
                },
                "wifi": {"network": "CozyBrew-Guest", "password": "coffee123"},
                "policies": {
                    "returns": "Free remake within 15 minutes, no questions asked.",
                    "loyalty": "Earn 1 point per $1 spent. 100 points = free drink.",
                    "pets": "Service animals welcome. Outdoor patio is pet-friendly.",
                },
            },
        }

    def log_conversation(self, session_id, message, sender):
        timestamp = datetime.now().isoformat()
        row = [timestamp, session_id, message, sender]
        return self._append_row_safe("Conversations", row)

    def create_ticket(self, session_id, issue):
        ticket_id = f"TKT-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        row = [ticket_id, session_id, issue, "Open", datetime.now().isoformat()]
        self._append_row_safe("Tickets", row)
        return ticket_id

    def get_all_tickets(self):
        return self._get_all_records_safe("Tickets")

    def get_all_conversations(self):
        return self._get_all_records_safe("Conversations")

    def create_order(self, session_id, items, total):
        order_id = f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        row = [order_id, session_id, items, total, "Pending", datetime.now().isoformat()]
        self._append_row_safe("Orders", row)
        return order_id

    def get_all_orders(self):
        return self._get_all_records_safe("Orders")


sheets = SheetsManager()