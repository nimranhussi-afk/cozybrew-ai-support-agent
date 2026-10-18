import os
import json
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional, Any

from dotenv import load_dotenv

load_dotenv()

from langchain_openai import ChatOpenAI

try:
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
except ImportError:
    try:
        from langchain.schema.messages import HumanMessage, AIMessage, SystemMessage
    except ImportError:
        class HumanMessage:
            def __init__(self, content): self.content = content; self.type = "human"


        class AIMessage:
            def __init__(self, content): self.content = content; self.type = "ai"


        class SystemMessage:
            def __init__(self, content): self.content = content; self.type = "system"

try:
    from langchain.memory import ConversationBufferMemory
except ImportError:
    class ConversationBufferMemory:
        def __init__(self, return_messages=False):
            self.return_messages = return_messages
            self.buffer = []

        def save_context(self, inputs, outputs): self.buffer.append({"input": inputs, "output": outputs})

        def load_memory_variables(self, _): return {"history": self.buffer} if self.return_messages else {"history": ""}

        def clear(self): self.buffer = []

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import sqlite3
from pydantic import BaseModel

try:
    from backend.database import init_db, save_message, get_conversation_history, create_ticket, DB_FILE
    from backend.sheets_manager import sheets
except ImportError:
    try:
        from .database import init_db, save_message, get_conversation_history, create_ticket, DB_FILE
        from .sheets_manager import sheets
    except ImportError:
        from database import init_db, save_message, get_conversation_history, create_ticket, DB_FILE
        from sheets_manager import sheets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
MODEL = os.getenv("MODEL", "openai/gpt-4o-mini")

FAQS_DATA = {}
faqs_path = os.path.join(os.path.dirname(__file__), "faqs.json")
if not os.path.exists(faqs_path): faqs_path = "faqs.json"
if os.path.exists(faqs_path):
    try:
        with open(faqs_path, "r", encoding="utf-8") as f:
            FAQS_DATA = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load faqs.json: {e}")

llm = ChatOpenAI(
    model=MODEL,
    openai_api_key=OPENAI_API_KEY,
    openai_api_base=OPENAI_BASE_URL,
    temperature=0.7,
    max_tokens=1000,
    default_headers={"HTTP-Referer": "http://localhost:8000", "X-Title": "CozyBrew Coffee Shop"}
)

ticket_flows: Dict[str, Dict[str, str]] = {}


def get_live_menu() -> Dict[str, List[Dict]]:
    base_menu = FAQS_DATA.get("menu", {"coffee": [], "food": []})
    if sheets.is_available:
        try:
            sheet_menu = sheets.get_menu()
            if sheet_menu:
                for cat, items in sheet_menu.items():
                    if cat not in base_menu:
                        base_menu[cat] = []
                    for item in items:
                        if item not in base_menu[cat]:
                            base_menu[cat].append(item)
        except Exception as e:
            logger.error(f"Failed to fetch live menu from sheets: {e}")
    return base_menu


def save_live_menu(menu_data: Dict):
    global FAQS_DATA
    FAQS_DATA["menu"] = menu_data
    try:
        with open(faqs_path, "w", encoding="utf-8") as f:
            json.dump(FAQS_DATA, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to write faqs.json: {e}")


def detect_intent(message: str) -> str:
    msg_lower = message.lower()
    if any(w in msg_lower for w in ["ord-", "status", "track", "where is my order", "check order"]):
        return "status"

    order_action_words = ["order", "i want", "i'd like", "give me", "buy", "purchase", "get me", "get a"]
    has_action = any(w in msg_lower for w in order_action_words)

    menu = get_live_menu()
    all_items = []
    for cat in menu.values():
        if isinstance(cat, list):
            for item in cat:
                all_items.append(item.get("name", "").lower())

    has_item = any(item in msg_lower for item in all_items if len(item) > 2)

    if has_action and has_item:
        return "order"
    if any(w in msg_lower for w in
           ["ticket", "complaint", "issue", "problem", "help", "support", "refund", "cancel", "frustrated"]):
        return "ticket"
    if any(w in msg_lower for w in ["hello", "hi", "hey"]):
        return "greeting"
    if any(w in msg_lower for w in ["thank", "thanks"]):
        return "thanks"
    return "general"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS order_status_overrides (
            order_id TEXT PRIMARY KEY,
            status TEXT
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            session_id TEXT,
            items TEXT,
            total REAL,
            status TEXT,
            created_at TEXT
        )''')
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB init override table error: {e}")
    yield


from fastapi.responses import FileResponse
import os

app = FastAPI(title="CozyBrew Coffee Shop Support", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ADD THIS ROOT ROUTE HERE ---
@app.get("/")
async def root():
    # Looks for frontend/chat.html relative to backend directory
    chat_file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "chat.html")
    if os.path.exists(chat_file_path):
        return FileResponse(chat_file_path)
    return {"message": "CozyBrew AI Support Agent API is live!", "docs": "/docs"}


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        self.active_connections[session_id] = websocket

    def disconnect(self, session_id: str):
        self.active_connections.pop(session_id, None)

    async def send_message(self, message: str, session_id: str):
        if session_id in self.active_connections:
            try:
                await self.active_connections[session_id].send_text(message)
            except Exception:
                self.disconnect(session_id)

    async def broadcast_status_update(self, target_session_id: str, order_id: str, new_status: str):
        if target_session_id in self.active_connections:
            payload = json.dumps({
                "type": "status_update",
                "message": f"🎉 **Update on order {order_id}**: Your order status has been updated to **{new_status}**! ☕"
            })
            await self.send_message(payload, target_session_id)


manager = ConnectionManager()


class StatusUpdate(BaseModel):
    status: str


class MenuItemAdd(BaseModel):
    name: str
    price: str
    category: str


@app.post("/api/admin/order/{order_id}/status")
async def update_order_status(order_id: str, payload: StatusUpdate):
    new_status = payload.status
    target_session_id = None
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        cursor.execute("SELECT session_id FROM orders WHERE order_id = ? OR id = ?", (order_id, order_id))
        row = cursor.fetchone()
        if row:
            target_session_id = row[0]

        cursor.execute("INSERT OR REPLACE INTO order_status_overrides (order_id, status) VALUES (?, ?)",
                       (order_id, new_status))
        cursor.execute("UPDATE orders SET status = ? WHERE order_id = ? OR id = ?", (new_status, order_id, order_id))
        conn.commit()
        conn.close()

        if target_session_id:
            await manager.broadcast_status_update(target_session_id, order_id, new_status)

        return {"success": True, "order_id": order_id, "status": new_status, "notified_session": target_session_id}
    except Exception as e:
        logger.error(f"Status update error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/menu/add")
async def add_menu_item(item: MenuItemAdd):
    current_menu = get_live_menu()
    cat = item.category.lower()
    if cat not in current_menu:
        current_menu[cat] = []

    current_menu[cat].append({"name": item.name, "price": item.price})
    save_live_menu(current_menu)
    return {"success": True, "menu": current_menu}


def get_html_file(filename: str) -> str:
    paths = [
        os.path.join(os.path.dirname(__file__), "..", "frontend", filename),
        os.path.join(os.getcwd(), "frontend", filename),
        filename,
    ]
    for path in paths:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    return ""


@app.get("/", response_class=HTMLResponse)
async def get():
    return HTMLResponse(get_html_file("chat.html"))


@app.get("/api/menu")
async def get_menu():
    return get_live_menu()


@app.get("/api/admin/data")
async def get_admin_data():
    formatted_tickets = [["Ticket ID", "Session", "Subject", "Priority", "Status", "Created"]]
    formatted_orders = [["Order ID", "Session", "Items", "Total", "Status", "Created"]]
    formatted_convos = [["Session", "User", "Bot", "Time"]]

    overrides = {}
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT order_id, status FROM order_status_overrides")
        for oid, st in cursor.fetchall():
            overrides[str(oid)] = st

        cursor.execute("SELECT id, session_id, issue, status, created_at FROM tickets")
        for row in cursor.fetchall():
            t_id = f"TKT-{row[0]:03d}"
            formatted_tickets.append([t_id, row[1], row[2], "Medium", str(row[3]).capitalize(), str(row[4])[:16]])

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='orders'")
        if cursor.fetchone():
            cursor.execute("SELECT order_id, session_id, items, total, status, created_at FROM orders")
            for row in cursor.fetchall():
                oid = row[0]
                st = overrides.get(str(oid), row[4])
                formatted_orders.append([oid, row[1], row[2], str(row[3]), st, str(row[5])[:16]])

        cursor.execute("SELECT session_id, message, sender, timestamp FROM conversations ORDER BY id ASC")
        db_messages = cursor.fetchall()

        conv_dict = {}
        for session, msg, sender, ts in db_messages:
            if session not in conv_dict:
                conv_dict[session] = {"user": "-", "bot": "-", "time": ts[:16] if ts else ""}
            if sender.lower() == "user":
                conv_dict[session]["user"] = msg
            elif sender.lower() in ["bot", "agent"]:
                conv_dict[session]["bot"] = msg
                formatted_convos.append(
                    [session, conv_dict[session]["user"], conv_dict[session]["bot"], conv_dict[session]["time"]])
                conv_dict[session] = {"user": "-", "bot": "-", "time": ts[:16] if ts else ""}

        conn.close()
    except Exception as e:
        logger.error(f"Error fetching SQLite admin data: {e}")

    return {
        "tickets": formatted_tickets,
        "orders": formatted_orders,
        "conversations": formatted_convos,
        "menu": get_live_menu(),
        "faqs": FAQS_DATA.get("faqs", []),
    }


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await manager.connect(websocket, session_id)
    memory = ConversationBufferMemory(return_messages=True)
    try:
        welcome = "Hi! Welcome to CozyBrew Coffee Shop. How can I help you today? ☕"
        await websocket.send_text(json.dumps({"type": "bot", "message": welcome}))
        memory.save_context({"input": "[system]"}, {"output": welcome})

        while True:
            data = await websocket.receive_text()
            try:
                message_data = json.loads(data)
                user_message = message_data.get("message", "")
            except:
                user_message = data
            if not user_message: continue

            bot_response = ""

            if session_id in ticket_flows:
                flow = ticket_flows[session_id]
                if "contact" not in flow:
                    flow["contact"] = user_message
                    bot_response = "Got it! Please briefly describe the issue or reason for generating this ticket."
                elif "reason" not in flow:
                    flow["reason"] = user_message
                    ticket_db_id = create_ticket(session_id, f"Contact: {flow['contact']} | Issue: {flow['reason']}")
                    ticket_id = sheets.create_ticket(session_id,
                                                     f"Contact: {flow['contact']} | Issue: {flow['reason']}") if sheets.is_available else f"TKT-{ticket_db_id:03d}"
                    bot_response = f"Thank you! Support ticket **{ticket_id}** has been created with contact info: **{flow['contact']}**. Our team will contact you shortly."
                    del ticket_flows[session_id]

            if not bot_response:
                intent = detect_intent(user_message)

                if intent == "ticket":
                    ticket_flows[session_id] = {}
                    bot_response = "I'm sorry to hear you're experiencing an issue! To help our support team contact you, could you please provide your **email address or phone number**?"

                elif intent == "status":
                    bot_response = "Your order is registered in our system and is currently being prepared by our baristas! ☕"

                elif intent == "order":
                    current_menu = get_live_menu()
                    menu_str = json.dumps(current_menu)
                    extract_prompt = (
                        f"Extract ONLY the specific items requested by the user in: '{user_message}' based on live menu: {menu_str}. "
                        "Do not include items the user did not ask for. "
                        "Return valid JSON strictly formatted as {\"items\": [{\"name\": \"...\", \"quantity\": 1, \"price\": \"...\"}], \"total\": 0.0}."
                    )
                    try:
                        extract_messages = [SystemMessage(content="Return JSON only."),
                                            HumanMessage(content=extract_prompt)]
                        extract_response = await llm.ainvoke(extract_messages)
                        clean_text = extract_response.content.replace("```json", "").replace("```", "").strip()
                        order_info = json.loads(clean_text)
                        items_list = order_info.get("items", [])
                        total_val = order_info.get("total", 0)

                        if items_list:
                            if not total_val or total_val == 0:
                                total_val = sum(
                                    float(str(i.get("price", "5.00")).replace("$", "")) * int(i.get("quantity", 1)) for
                                    i in items_list)
                            order_id = sheets.create_order(session_id, json.dumps(items_list), float(
                                total_val)) if sheets.is_available else f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}"

                            try:
                                conn = sqlite3.connect(DB_FILE)
                                cur = conn.cursor()
                                cur.execute(
                                    "INSERT OR REPLACE INTO orders (order_id, session_id, items, total, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                                    (order_id, session_id, json.dumps(items_list), float(total_val), "Pending",
                                     datetime.now().isoformat()))
                                conn.commit()
                                conn.close()
                            except Exception as db_err:
                                logger.error(f"Local order save error: {db_err}")

                            item_names = ", ".join(
                                [f"{i.get('quantity', 1)}x {i.get('name', 'Item')}" for i in items_list])
                            bot_response = f"Got it! I've placed your order for **{item_names}**. Order ID: **{order_id}** | Total: **${float(total_val):.2f}**. Status: Pending ☕"
                        else:
                            bot_response = "I'd love to help you order! Check out our Menu tab on the website to see what we have available!"
                    except Exception as e:
                        logger.error(f"Order error: {e}")
                        bot_response = "I'd love to help you order! What item from our live menu would you like?"

                elif intent == "greeting":
                    bot_response = "Hello! Welcome to CozyBrew Coffee Shop. What can I get for you today? ☕"
                elif intent == "thanks":
                    bot_response = "You're very welcome! Enjoy your coffee! ☕"
                else:
                    raw_history = memory.load_memory_variables({}).get("history", [])
                    bot_response = await generate_response(user_message, session_id, intent, raw_history)

            memory.save_context({"input": user_message}, {"output": bot_response})
            save_message(session_id, user_message, "user")
            save_message(session_id, bot_response, "bot")
            if sheets.is_available:
                sheets.log_conversation(session_id, user_message, "user")
                sheets.log_conversation(session_id, bot_response, "bot")

            await manager.send_message(json.dumps({"type": "bot", "message": bot_response}), session_id)

    except WebSocketDisconnect:
        logger.info(f"Client disconnected: {session_id}")
    finally:
        manager.disconnect(session_id)


async def generate_response(message: str, session_id: str, intent: str, context: Any) -> str:
    system = f"You are CozyBrew Coffee Shop assistant. Live Menu: {json.dumps(get_live_menu())}. Answer customer queries warmly."
    try:
        messages = [SystemMessage(content=system), HumanMessage(content=message)]
        res = await llm.ainvoke(messages)
        return res.content
    except Exception as e:
        return "How else can I help you at CozyBrew today?"


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")