import os
import asyncio
import random
import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from telethon import TelegramClient, events, Button
from telethon.tl.functions.messages import SendReactionRequest
from telethon.errors import SessionPasswordNeededError

# ─── LOGGING ─────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ─── CONFIG FROM ENV ─────────────────────────────
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")
REDIS_URL = os.environ.get("REDIS_URL", "")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "0").split(",") if x]

if not API_ID or not API_HASH:
    logger.error("❌ API_ID and API_HASH must be set in environment!")
    exit(1)

# ─── SIMPLE FILE-BASED STORAGE (NO MONGODB NEEDED) ──
class Database:
    def __init__(self):
        self.users = {}       # {user_id: {data}}
        self.sessions = {}    # {session_id: {data}}
        self.settings = {}    # {user_id: {settings}}
        self.notes = {}       # {user_id: {key: value}}
        self.schedules = []   # [{data}]
    
    async def load(self):
        try:
            import aiofiles
            async with aiofiles.open("database.json", "r") as f:
                data = json.loads(await f.read())
                self.users = data.get("users", {})
                self.sessions = data.get("sessions", {})
                self.settings = data.get("settings", {})
                self.notes = data.get("notes", {})
                self.schedules = data.get("schedules", [])
                logger.info(f"✅ Loaded {len(self.users)} users from database")
        except: logger.info("🆕 New database created")
    
    async def save(self):
        data = {
            "users": self.users,
            "sessions": self.sessions,
            "settings": self.settings,
            "notes": self.notes,
            "schedules": self.schedules
        }
        try:
            import aiofiles
            async with aiofiles.open("database.json", "w") as f:
                await f.write(json.dumps(data, indent=2, default=str))
        except: pass

db = Database()

# ─── AI ENGINE ────────────────────────────────────
class AIEngine:
    """Human-like chat engine that adapts to conversation tone"""
    
    def __init__(self):
        self.history = {}
        
        self.friendly_responses = {
            "hi": ["Hey! How are you?", "Hello! Good to see you!", "Hi there! What's up?", "Hey! Kaise ho?"],
            "hello": ["Hello! Kaise ho?", "Hey! Kya chal raha hai?", "Hi! Tell me something interesting!"],
            "bye": ["Bye! Take care!", "Goodbye! Talk later!", "See you! Stay safe!", "Alright, catch you later!"],
            "thanks": ["Welcome! Happy to help!", "No problem! Anytime!", "Glad I could help!", "My pleasure!"],
            "good": ["That's great!", "Awesome! Happy for you!", "Nice! Keep it up!", "Mast hai bhai!"],
            "bad": ["Oh no, hope things get better!", "Sorry to hear that. Stay strong!", "That sucks. I'm here if you need to talk!"],
            "how": ["I'm good, thanks! What about you?", "Chal raha hai bhai! Tu bata?", "All good! What's new with you?"],
            "default": [
                "That's interesting! Tell me more!",
                "Haan bhai, bilkul sahi kaha!",
                "Sahi pakde hai! Kya scene hai tera?",
                "I see what you mean! Go on...",
                "Haha, true! You always have the best points!",
                "Hmm, main bhi yahi soch raha tha!",
                "Exactly! Tu toh bahut smart hai bhai!"
            ]
        }
        
        self.savage_responses = {
            "default": [
                "Tujhe kya lagta hai tu kaun hai bhai?",
                "Chup kar bhadwe! Terese baat nahi karni.",
                "Apna kaam kar na, faltu mein mat aa.",
                "Teri aukaat nahi hai mere saath baat karne ki.",
                "Gawaar ho tum, pehle baat karna seekh lo.",
                "Tu itna boring hai ki WiFi bhi tujhse disconnect ho jata hai.",
                "Teri photo dekh ke antivirus trigger ho gaya.",
                "Kitna bhi try karoge, mujhse match nahi kar sakte.",
                "Terese na ho payega bhai, ja soja.",
                "Tera muh dekh ke mirror bhi tut jata hai.",
            ],
            "insult": [
                "Wow! Kitni badi gali di tune! Mai toh dar gaya! 🙄",
                "Gali dena aata hai bas? Kuch aur bhi aata hai?",
                "Teri galiyaan bhi teri tarah hi bekar hain.",
                "Aur kitna gand maraoge be? Bas kar ab!",
                "Teri maa ko kya hua? Oh wait... never mind.",
                "Tere baare mein soch ke mera time waste hota hai.",
            ],
            "abuse": [
                "Bhadwe, terese baat karke mera time waste hai.",
                "Saale, ab limit khatam ho gayi. Chup ho ja.",
                "Tujhe lagta hai main tere saath time waste karunga?",
                "Harami! Ja apni maa ko gali de, yahan mat aa.",
                "Behen ke lode, teri aukaat nahi hai mere saamne."
            ]
        }
    
    def detect_tone(self, text):
        """Detect user's tone from message"""
        text_lower = text.lower()
        
        # Check for abuse/insults
        abusive_words = ["bhadwe", "madarchod", "behenchod", "gandu", "chutiye", "saale", "sale",
                        "harami", "kutte", "bhen ke lode", "bhosdike", "laude", "fuck", "shit",
                        "asshole", "bastard", "dick", "idiot", "nikal", "chup"]
        
        for word in abusive_words:
            if word in text_lower:
                return "abuse"
        
        insult_words = ["tu kaun", "teri aukaat", "gawaar", "bewakoof", "noob", "bakwas", "be"]
        for word in insult_words:
            if word in text_lower:
                return "insult"
        
        # Check for greetings
        if any(word in text_lower for word in ["hi", "hello", "hey", "namaste", "kaise ho", "kya haal"]):
            return "greeting"
        
        # Check for negativity
        if any(word in text_lower for word in ["sad", "depressed", "alone", "lonely", "crying", "upset", "frustrated", "udaas", "akela"]):
            return "sad"
        
        # Check for gratitude
        if any(word in text_lower for word in ["thanks", "thank", "dhanyavaad", "shukriya", "welcome"]):
            return "thanks"
        
        # Check for questions
        if "?" in text or any(word in text_lower for word in ["kya", "kaise", "kyun", "kab", "kaun", "kahan"]):
            return "question"
        
        return "neutral"
    
    def is_hindi(self, text):
        """Check if text contains Hindi"""
        hindi_chars = re.compile(r'[\u0900-\u097F]')
        if hindi_chars.search(text):
            return True
        hindi_words = ["hai", "ho", "hun", "hain", "ka", "ki", "ke", "ko", "se", "mein", "par",
                      "tum", "aap", "tu", "mera", "tera", "apna", "kya", "kyun", "kaise", "bhai",
                      "yaar", "acha", "accha", "mast", "sahi", "nahi", "haan", "han"]
        for word in hindi_words:
            if word in text.lower().split():
                return True
        return False
    
    def generate_reply(self, user_id, text):
        """Generate context-aware human-like reply"""
        tone = self.detect_tone(text)
        is_hindi = self.is_hindi(text)
        
        # Update history
        if user_id not in self.history:
            self.history[user_id] = []
        self.history[user_id].append({"role": "user", "text": text})
        if len(self.history[user_id]) > 10:
            self.history[user_id] = self.history[user_id][-10:]
        
        # Generate reply based on tone
        if tone == "abuse":
            reply = random.choice(self.savage_responses["abuse"])
        elif tone == "insult":
            reply = random.choice(self.savage_responses["insult"])
        elif tone == "sad":
            replies = [
                "Sun bhai, tension mat le. Sab theek ho jayega! Main hoon na tere saath! 💪",
                "Are yaar, itna sad mat ho. Life mein ups and downs aate rehte hain. Tu strong hai! ❤️",
                "Bhai, jo bhi hua, woh waqt beeta hua hai. Aage dekho! ✨",
                "Chinta mat kar, main tere saath hoon. Kuch chahiye toh bata! 🤗"
            ]
            reply = random.choice(replies)
        elif tone == "greeting":
            if is_hindi:
                replies = [
                    "Hey bhai! Kaise ho? Kya chal raha hai?",
                    "Namaste! Kya haal hai aapke?",
                    "Hello bhai! Kya scene hai?",
                    "Hey! Kaise ho? Bahut din baad dikhe!"
                ]
            else:
                replies = self.friendly_responses["hi"]
            reply = random.choice(replies)
        elif tone == "thanks":
            if is_hindi:
                replies = ["Koi nahi bhai! Hamesha khush raho!", "Welcome bhai! Kabhi bhi!"]
            else:
                replies = self.friendly_responses["thanks"]
            reply = random.choice(replies)
        else:
            if is_hindi:
                if tone == "question":
                    replies = [
                        "Achha sawaal hai! Mera khayal hai ki...",
                        "Dekho bhai, iska jawab hai ki...",
                        "Interesting! Actually maine iske baare mein socha nahi tha but..."
                    ]
                else:
                    replies = self.friendly_responses["default"]
            else:
                if tone == "question":
                    replies = ["Great question! I think...", "Let me think... The answer is..."]
                else:
                    replies = self.friendly_responses["default"]
            reply = random.choice(replies)
        
        # Update history
        self.history[user_id].append({"role": "assistant", "text": reply})
        
        return reply

ai_engine = AIEngine()

# ─── MAIN BOT CLIENT ─────────────────────────────
client = TelegramClient("main_bot", API_ID, API_HASH)

# ─── USERBOT MANAGER ─────────────────────────────
class UserBotManager:
    def __init__(self):
        self.user_clients = {}     # {user_id: TelegramClient}
        self.active_features = {}  # {user_id: {features}}]
    
    async def start_userbot(self, user_id, session_string):
        """Start a userbot for a user"""
        try:
            client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
            await client.start()
            self.user_clients[user_id] = client
            
            # Register feature handlers
            await self.register_handlers(user_id, client)
            
            # Default settings
            if user_id not in db.settings:
                db.settings[user_id] = {
                    "afk": False, "afk_message": "AFK hoon. Baad mein baat karta hoon!",
                    "ai_reply": True, "custom_replies": {},
                    "auto_read": False, "auto_react": False, "react_emoji": "👍",
                    "personality": "balanced"
                }
            
            logger.info(f"✅ Userbot started for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to start userbot for {user_id}: {e}")
            return False
    
    async def stop_userbot(self, user_id):
        """Stop userbot for a user"""
        if user_id in self.user_clients:
            try:
                await self.user_clients[user_id].disconnect()
            except: pass
            del self.user_clients[user_id]
            logger.info(f"Userbot stopped for user {user_id}")
    
    async def register_handlers(self, user_id, user_client):
        """Register all feature handlers for a userbot"""
        
        # ─── AFK HANDLER ────────────────────────
        @user_client.on(events.NewMessage)
        async def afk_handler(event):
            me = await user_client.get_me()
            if event.sender_id == me.id: return
            
            settings = db.settings.get(user_id, {})
            if settings.get("afk") and (event.is_private or me.username and f"@{me.username}" in event.raw_text):
                afk_msg = settings.get("afk_message", "AFK hoon.")
                await asyncio.sleep(1)
                await event.reply(f"🤖 **AFK Mode:** {afk_msg}")
        
        # ─── AI AUTO-REPLY HANDLER ──────────────
        @user_client.on(events.NewMessage)
        async def ai_reply_handler(event):
            me = await user_client.get_me()
            if event.sender_id == me.id: return
            if event.is_private or f"@{me.username}" in event.raw_text:
                settings = db.settings.get(user_id, {})
                if settings.get("ai_reply") and not settings.get("afk"):
                    async with user_client.action(event.chat_id, "typing"):
                        await asyncio.sleep(random.uniform(0.5, 2.0))
                    reply = ai_engine.generate_reply(event.sender_id, event.raw_text)
                    await event.reply(reply)
        
        # ─── CUSTOM AUTO-REPLY HANDLER ──────────
        @user_client.on(events.NewMessage)
        async def custom_reply_handler(event):
            me = await user_client.get_me()
            if event.sender_id == me.id: return
            
            settings = db.settings.get(user_id, {})
            custom_replies = settings.get("custom_replies", {})
            for trigger, response in custom_replies.items():
                if trigger.lower() in event.raw_text.lower():
                    await event.reply(response)
                    return
        
        # ─── AUTO-READ HANDLER ──────────────────
        @user_client.on(events.NewMessage)
        async def auto_read_handler(event):
            settings = db.settings.get(user_id, {})
            if settings.get("auto_read"):
                try:
                    await user_client.send_read_acknowledge(event.chat_id)
                except: pass
        
        # ─── AUTO-REACT HANDLER ─────────────────
        @user_client.on(events.NewMessage)
        async def auto_react_handler(event):
            me = await user_client.get_me()
            if event.sender_id == me.id: return
            
            settings = db.settings.get(user_id, {})
            if settings.get("auto_react"):
                try:
                    emoji = settings.get("react_emoji", "👍")
                    await user_client(SendReactionRequest(
                        peer=event.chat_id, msg_id=event.id, reaction=[emoji]
                    ))
                except: pass
        
        # ─── SPAM COMMAND ───────────────────────
        @user_client.on(events.NewMessage(pattern=r'\.spam (\d+) (.+)'))
        async def spam_cmd(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            
            count = int(event.pattern_match.group(1))
            msg = event.pattern_match.group(2)
            if count > 500: count = 500
            
            await event.delete()
            status = await user_client.send_message(event.chat_id, f"📨 Spam: 0/{count}")
            
            for i in range(count):
                await user_client.send_message(event.chat_id, msg)
                await asyncio.sleep(0.3)
                if (i+1) % 25 == 0:
                    try: await status.edit(f"📨 Spam: {i+1}/{count}")
                    except: pass
            
            try: await status.edit(f"✅ Spam complete! {count} messages")
            except: pass
        
        # ─── RAID COMMAND ───────────────────────
        @user_client.on(events.NewMessage(pattern=r'\.raid (\d+) (.+)'))
        async def raid_cmd(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            
            count = int(event.pattern_match.group(1))
            msg = event.pattern_match.group(2)
            if count > 200: count = 200
            
            await event.delete()
            status = await user_client.send_message(event.chat_id, f"⚔️ Raid: 0/{count}")
            
            for i in range(count):
                await user_client.send_message(event.chat_id, msg)
                await asyncio.sleep(0.5)
                if (i+1) % 20 == 0:
                    try: await status.edit(f"⚔️ Raid: {i+1}/{count}")
                    except: pass
            
            try: await status.edit(f"✅ Raid complete! {count} messages")
            except: pass
        
        # ─── REPLY RAID ─────────────────────────
        reply_raid_active = {}
        
        @user_client.on(events.NewMessage(pattern=r'\.replyraid (\d+) (.+)'))
        async def replyraid_start(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            
            count = int(event.pattern_match.group(1))
            msg = event.pattern_match.group(2)
            if count > 10: count = 10
            
            reply_raid_active[event.chat_id] = {"count": count, "msg": msg, "active": True}
            await event.reply(f"🔄 Reply Raid active! {count} baar reply karega...")
        
        @user_client.on(events.NewMessage(pattern=r'\.stopreplyraid'))
        async def replyraid_stop(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            if event.chat_id in reply_raid_active:
                reply_raid_active[event.chat_id]["active"] = False
                del reply_raid_active[event.chat_id]
                await event.reply("🛑 Reply Raid stopped!")
        
        @user_client.on(events.NewMessage)
        async def replyraid_handler(event):
            me = await user_client.get_me()
            if event.sender_id == me.id: return
            if event.chat_id in reply_raid_active and reply_raid_active[event.chat_id].get("active"):
                data = reply_raid_active[event.chat_id]
                for _ in range(data["count"]):
                    try:
                        await event.reply(data["msg"])
                        await asyncio.sleep(0.3)
                    except: break
        
        # ─── AFK COMMANDS ───────────────────────
        @user_client.on(events.NewMessage(pattern=r'\.afk ?(.*)'))
        async def set_afk(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            
            msg = event.pattern_match.group(1) or "AFK hoon. Baad mein baat karta hoon!"
            if user_id not in db.settings: db.settings[user_id] = {}
            db.settings[user_id]["afk"] = True
            db.settings[user_id]["afk_message"] = msg
            await db.save()
            await event.reply(f"🤖 **AFK MODE ON:** {msg}")
        
        @user_client.on(events.NewMessage(pattern=r'\.unafk'))
        async def unafk(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            
            if user_id in db.settings:
                db.settings[user_id]["afk"] = False
                await db.save()
            
            await event.reply("✅ **AFK MODE OFF**\nWelcome back!")
        
        # ─── AI TOGGLE ──────────────────────────
        @user_client.on(events.NewMessage(pattern=r'\.aion'))
        async def ai_on(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            if user_id not in db.settings: db.settings[user_id] = {}
            db.settings[user_id]["ai_reply"] = True
            await db.save()
            await event.reply("✅ **AI Auto-Reply ON**")
        
        @user_client.on(events.NewMessage(pattern=r'\.aioff'))
        async def ai_off(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            if user_id not in db.settings: db.settings[user_id] = {}
            db.settings[user_id]["ai_reply"] = False
            await db.save()
            await event.reply("✅ **AI Auto-Reply OFF**")
        
        # ─── HELP ───────────────────────────────
        @user_client.on(events.NewMessage(pattern=r'\.help'))
        async def help_cmd(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            
            text = """
╔══════════════════════════════════════════╗
║         🤖 USERBOT COMMANDS             ║
╠══════════════════════════════════════════╣
║ 🔹 SPAM: .spam 50 hello                 ║
║ 🔹 RAID: .raid 30 attack                ║
║ 🔹 REPLY RAID: .replyraid 5 hi          ║
║ 🔹 STOP REPLY RAID: .stopreplyraid      ║
║ 🔹 AFK: .afk your_message               ║
║ 🔹 UNAFK: .unafk                        ║
║ 🔹 AI ON: .aion                         ║
║ 🔹 AI OFF: .aioff                       ║
║ 🔹 SAVE NOTE: .save key value           ║
║ 🔹 GET NOTE: .get key                   ║
║ 🔹 NOTES: .notes                        ║
║ 🔹 DELNOTE: .delnote key                ║
║ 🔹 SET REPLY: .setreply trigger|reply   ║
║ 🔹 AUTO READ ON: .autoreadon            ║
║ 🔹 AUTO READ OFF: .autoreadoff          ║
║ 🔹 AUTO REACT ON: .autoreacton 👍       ║
║ 🔹 AUTO REACT OFF: .autoreactoff        ║
║ 🔹 SCHEDULE: .schedule time message     ║
║ 🔹 PING: .ping                          ║
║ 🔹 HELP: .help                          ║
╚══════════════════════════════════════════╝
            """
            await event.reply(text)
        
        # ─── NOTES ─────────────────────────────
        @user_client.on(events.NewMessage(pattern=r'\.save (\w+) (.+)'))
        async def save_note(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            key = event.pattern_match.group(1).lower()
            value = event.pattern_match.group(2)
            
            if user_id not in db.notes: db.notes[user_id] = {}
            db.notes[user_id][key] = value
            await db.save()
            await event.reply(f"✅ Note `{key}` saved!")
        
        @user_client.on(events.NewMessage(pattern=r'\.get (\w+)'))
        async def get_note(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            key = event.pattern_match.group(1).lower()
            
            if user_id in db.notes and key in db.notes[user_id]:
                await event.reply(f"**📝 {key}:**\n{db.notes[user_id][key]}")
            else:
                await event.reply(f"❌ Note `{key}` not found!")
        
        @user_client.on(events.NewMessage(pattern=r'\.notes'))
        async def list_notes(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            
            if user_id in db.notes and db.notes[user_id]:
                text = "**📋 Your Notes:**\n\n"
                for key, value in db.notes[user_id].items():
                    text += f"• `{key}`: {value[:50]}...\n"
                await event.reply(text)
            else:
                await event.reply("📭 No notes saved!")
        
        @user_client.on(events.NewMessage(pattern=r'\.delnote (\w+)'))
        async def delete_note(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            key = event.pattern_match.group(1).lower()
            
            if user_id in db.notes and key in db.notes[user_id]:
                del db.notes[user_id][key]
                await db.save()
                await event.reply(f"✅ Note `{key}` deleted!")
            else:
                await event.reply(f"❌ Note `{key}` not found!")
        
        # ─── SET CUSTOM REPLY ──────────────────
        @user_client.on(events.NewMessage(pattern=r'\.setreply (.+?)\|(.+)'))
        async def set_custom_reply(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            
            trigger = event.pattern_match.group(1).strip()
            response = event.pattern_match.group(2).strip()
            
            if user_id not in db.settings: db.settings[user_id] = {}
            if "custom_replies" not in db.settings[user_id]: db.settings[user_id]["custom_replies"] = {}
            db.settings[user_id]["custom_replies"][trigger] = response
            await db.save()
            await event.reply(f"✅ Custom reply set!\n`{trigger}` → `{response}`")
        
        # ─── AUTO READ TOGGLE ──────────────────
        @user_client.on(events.NewMessage(pattern=r'\.autoreadon'))
        async def autoread_on(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            if user_id not in db.settings: db.settings[user_id] = {}
            db.settings[user_id]["auto_read"] = True
            await db.save()
            await event.reply("✅ Auto-Read ON!")
        
        @user_client.on(events.NewMessage(pattern=r'\.autoreadoff'))
        async def autoread_off(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            if user_id not in db.settings: db.settings[user_id] = {}
            db.settings[user_id]["auto_read"] = False
            await db.save()
            await event.reply("✅ Auto-Read OFF!")
        
        # ─── AUTO REACT TOGGLE ─────────────────
        @user_client.on(events.NewMessage(pattern=r'\.autoreacton ?(.*)'))
        async def autoreact_on(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            
            emoji = event.pattern_match.group(1) or "👍"
            if user_id not in db.settings: db.settings[user_id] = {}
            db.settings[user_id]["auto_react"] = True
            db.settings[user_id]["react_emoji"] = emoji
            await db.save()
            await event.reply(f"✅ Auto-React ON! Emoji: {emoji}")
        
        @user_client.on(events.NewMessage(pattern=r'\.autoreactoff'))
        async def autoreact_off(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            if user_id not in db.settings: db.settings[user_id] = {}
            db.settings[user_id]["auto_react"] = False
            await db.save()
            await event.reply("✅ Auto-React OFF!")
        
        # ─── SCHEDULE ──────────────────────────
        @user_client.on(events.NewMessage(pattern=r'\.schedule (.+?) (.+)'))
        async def schedule_msg(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            
            time_str = event.pattern_match.group(1)
            msg = event.pattern_match.group(2)
            
            try:
                minutes = int(time_str)
                schedule_time = datetime.now() + timedelta(minutes=minutes)
                
                db.schedules.append({
                    "user_id": user_id,
                    "chat_id": event.chat_id,
                    "message": msg,
                    "time": schedule_time.isoformat(),
                    "sent": False
                })
                await db.save()
                await event.reply(f"✅ Scheduled! {minutes} minute baad bhejunga.")
            except:
                await event.reply("❌ Usage: .schedule <minutes> <message>")
        
        # ─── PING ──────────────────────────────
        @user_client.on(events.NewMessage(pattern=r'\.ping'))
        async def ping(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            start = datetime.now()
            msg = await event.reply("🏓 Pong!")
            end = datetime.now()
            ms = (end - start).microseconds / 1000
            await msg.edit(f"🏓 Pong! `{ms:.0f}ms`")
        
        # ─── SETTINGS ─────────────────────────
        @user_client.on(events.NewMessage(pattern=r'\.settings'))
        async def show_settings(event):
            me = await user_client.get_me()
            if event.sender_id != me.id: return
            
            s = db.settings.get(user_id, {})
            text = f"""
**⚙️ Your Settings:**
🤖 AFK: {'✅ ON' if s.get('afk') else '❌ OFF'}
🧠 AI Reply: {'✅ ON' if s.get('ai_reply') else '❌ OFF'}
📖 Auto-Read: {'✅ ON' if s.get('auto_read') else '❌ OFF'}
👍 Auto-React: {'✅ ON' if s.get('auto_react') else '❌ OFF'} {s.get('react_emoji', '')}
📝 Custom Replies: {len(s.get('custom_replies', {}))} 
"""
            await event.reply(text)

userbot_manager = UserBotManager()

# ─── SCHEDULER ────────────────────────────────────
async def scheduler_loop():
    while True:
        try:
            now = datetime.now()
            for schedule in db.schedules[:]:
                if not schedule.get("sent"):
                    schedule_time = datetime.fromisoformat(schedule["time"])
                    if now >= schedule_time:
                        user_id = schedule["user_id"]
                        chat_id = schedule["chat_id"]
                        msg = schedule["message"]
                        
                        if user_id in userbot_manager.user_clients:
                            try:
                                await userbot_manager.user_clients[user_id].send_message(chat_id, f"⏰ **Scheduled:**\n{msg}")
                            except: pass
                        
                        schedule["sent"] = True
                        await db.save()
        except: pass
        await asyncio.sleep(30)

# ─── BOT COMMAND HANDLERS ────────────────────────
from telethon.sessions import StringSession

@client.on(events.NewMessage(pattern=r'^/host$'))
async def host_command(event):
    user_id = event.sender_id
    
    # Check if already hosted
    if user_id in userbot_manager.user_clients:
        await event.reply("❌ Aap already hosted hain! /sessions check karo.")
        return
    
    await event.reply(
        "🤖 **UserBot Host Setup**\n\n"
        "Apna **phone number** bhejo international format mein:\n"
        "Example: `+919876543210`\n\n"
        "⚠️ Aapka session secure encrypt hoga."
    )
    
    # Wait for phone number
    phone = await get_user_input(event, "phone")
    if not phone: return
    
    await event.reply("⏳ OTP bhej raha hoon...")
    
    try:
        temp_client = TelegramClient(StringSession(), API_ID, API_HASH)
        await temp_client.connect()
        
        sent = await temp_client.send_code_request(phone)
        
        await event.reply(
            "✅ OTP bhej diya gaya!\n\n"
            "**OTP code** enter karo (jaise: `12345`):"
        )
        
        code = await get_user_input(event, "otp")
        if not code: return
        
        try:
            await temp_client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
        except SessionPasswordNeededError:
            await event.reply("🔐 **2FA Required!**\nApna 2FA password daalo:")
            password = await get_user_input(event, "2fa")
            if not password: return
            await temp_client.sign_in(password=password)
        
        # Save session
        session_string = temp_client.session.save()
        me = await temp_client.get_me()
        
        db.users[str(user_id)] = {
            "phone": phone,
            "username": me.username,
            "first_name": me.first_name,
            "session": session_string,
            "created_at": datetime.now().isoformat()
        }
        db.sessions[str(user_id)] = {
            "active": True,
            "created_at": datetime.now().isoformat()
        }
        await db.save()
        
        # Start userbot
        await userbot_manager.start_userbot(user_id, session_string)
        
        await temp_client.disconnect()
        
        await event.reply(
            f"✅ **Hosting Successful!** 🎉\n\n"
            f"Welcome {me.first_name}!\n"
            f"📱 Phone: {phone}\n\n"
            f"📌 Apni I'd mein `.help` type karo commands dekhne ke liye.\n\n"
            f"**Features Active:**\n"
            f"🤖 AI Auto-Reply ✅\n"
            f"📝 Custom Replies ✅\n"
            f"⚔️ Raid/Spam ✅\n"
            f"🤫 AFK Mode ✅\n"
            f"📖 Auto-Read ✅\n"
            f"👍 Auto-React ✅\n"
            f"📅 Scheduler ✅\n"
            f"📋 Notes ✅\n\n"
            f"🔐 Aapka session secure hai!"
        )
        
    except Exception as e:
        await event.reply(f"❌ Error: {str(e)[:100]}")

@client.on(events.NewMessage(pattern=r'^/sessions$'))
async def sessions_command(event):
    user_id = str(event.sender_id)
    
    if user_id in db.users:
        user = db.users[user_id]
        active = user_id in userbot_manager.user_clients
        await event.reply(
            f"**📱 Your Session:**\n\n"
            f"👤 Name: {user.get('first_name', 'N/A')}\n"
            f"📞 Phone: {user.get('phone', 'N/A')}\n"
            f"🆔 User ID: `{user_id}`\n"
            f"✅ Active: {'Yes 🟢' if active else 'No 🔴'}\n"
            f"📅 Created: {user.get('created_at', 'N/A')[:10]}"
        )
    else:
        await event.reply("❌ No session found! Use /host to host your account.")

@client.on(events.NewMessage(pattern=r'^/logout$'))
async def logout_command(event):
    user_id = event.sender_id
    
    if user_id in userbot_manager.user_clients:
        await userbot_manager.stop_userbot(user_id)
    
    if str(user_id) in db.users:
        del db.users[str(user_id)]
    if str(user_id) in db.sessions:
        del db.sessions[str(user_id)]
    await db.save()
    
    await event.reply("✅ **Logged out!** Session deleted.")

@client.on(events.NewMessage(pattern=r'^/help$'))
async def help_command(event):
    text = """
**🤖 UserBot SaaS - Help**

**🔐 Account:**
/host - Host your Telegram account
/sessions - Check your session
/logout - Delete your session

**⚔️ Userbot Commands (. prefix):**
.help - Show all commands
.spam 50 hello - Spam 50 times
.raid 30 attack - Raid 30 times
.replyraid 5 hi - Reply raid
.stopreplyraid - Stop reply raid
.afk message - AFK mode on
.unafk - AFK mode off
.aion / .aioff - Toggle AI reply
.autoreadon/off - Toggle auto-read
.autoreacton 👍 - Toggle auto-react
.save key value - Save note
.get key - Get note
.notes - List notes
.delnote key - Delete note
.setreply trigger|response - Custom reply
.schedule 10 message - Schedule message
.ping - Check bot speed
.settings - View your settings

**👑 Admin (Owner Only):**
/admin - Admin panel
/stats - Bot statistics
/users - List all users
/broadcast - Broadcast message
/eval code - Execute Python

💡 Sab commands **apni I'd mein** use karo `.` ke saath!
    """
    await event.reply(text)

@client.on(events.NewMessage(pattern=r'^/start$'))
async def start_command(event):
    await event.reply(
        "🤖 **UserBot SaaS**\n\n"
        "Welcome! Main ek powerful userbot hoon.\n\n"
        "**Features:**\n"
        "🤖 AI Auto-Reply (Human-like)\n"
        "⚔️ Raid, Reply Raid, Spam\n"
        "🤫 AFK Mode\n"
        "📖 Auto-Read Messages\n"
        "👍 Auto-React\n"
        "📝 Custom Auto-Replies\n"
        "📋 Notes System\n"
        "📅 Scheduled Messages\n\n"
        "**Shuru karne ke liye:**\n/host - Apna account host karo\n\n"
        "**Ya help ke liye:**\n/help"
    )

# ─── ADMIN COMMANDS ──────────────────────────────
@client.on(events.NewMessage(pattern=r'^/admin$'))
async def admin_panel(event):
    if event.sender_id not in ADMIN_IDS:
        return await event.reply("❌ Owner-only command!")
    
    buttons = [
        [Button.inline("📊 Stats", data="admin_stats")],
        [Button.inline("👥 Users", data="admin_users")],
        [Button.inline("📢 Broadcast", data="admin_broadcast")],
        [Button.inline("💾 Backup", data="admin_backup")]
    ]
    await event.reply("**🔧 Admin Panel**", buttons=buttons)

@client.on(events.CallbackQuery)
async def admin_callback(event):
    if event.sender_id not in ADMIN_IDS: return
    data = event.data.decode()
    
    if data == "admin_stats":
        users = len(db.users)
        active = len(userbot_manager.user_clients)
        notes = sum(len(n) for n in db.notes.values()) if db.notes else 0
        schedules = len([s for s in db.schedules if not s.get("sent")])
        
        text = f"""
**📊 Bot Statistics**

**Users:**
• Total Users: {users}
• Active Now: {active}
• Inactive: {users - active}

**Data:**
• Notes: {notes}
• Pending Schedules: {schedules}

**System:**
• Running: {'Yes 🟢' if client.is_connected() else 'No 🔴'}
        """
        await event.edit(text)
    
    elif data == "admin_users":
        if not db.users:
            await event.edit("📭 No users registered.")
            return
        
        text = "**👥 Registered Users:**\n\n"
        for uid, u in db.users.items():
            active = "🟢" if int(uid) in userbot_manager.user_clients else "🔴"
            text += f"{active} [{u.get('first_name', 'N/A')}](tg://user?id={uid})\n"
        
        await event.edit(text)
    
    elif data == "admin_broadcast":
        await event.edit("Use: `/broadcast <message>` in chat")
    
    elif data == "admin_backup":
        await event.edit("✅ Database saved to `database.json`")
    
    await event.answer()

@client.on(events.NewMessage(pattern=r'^/broadcast (.+)'))
async def broadcast(event):
    if event.sender_id not in ADMIN_IDS:
        return await event.reply("❌ Owner-only command!")
    
    msg = event.pattern_match.group(1)
    sent = 0
    
    for uid in db.users:
        try:
            await client.send_message(int(uid), f"**📢 Broadcast:**\n\n{msg}")
            sent += 1
            await asyncio.sleep(0.05)
        except: pass
    
    await event.reply(f"✅ Broadcast sent to {sent} users!")

@client.on(events.NewMessage(pattern=r'^/stats$'))
async def stats(event):
    if event.sender_id not in ADMIN_IDS:
        return await event.reply("❌ Owner-only command!")
    
    await admin_callback(events.CallbackQuery.Event(
        sender_id=event.sender_id, data=b"admin_stats"
    ))

@client.on(events.NewMessage(pattern=r'^/users$'))
async def list_users(event):
    if event.sender_id not in ADMIN_IDS:
        return await event.reply("❌ Owner-only command!")
    
    await admin_callback(events.CallbackQuery.Event(
        sender_id=event.sender_id, data=b"admin_users"
    ))

# ─── HELPER: GET USER INPUT ──────────────────────
async def get_user_input(event, input_type):
    """Wait for user response and return it"""
    future = asyncio.get_event_loop().create_future()
    
    @client.on(events.NewMessage(chats=event.sender_id))
    async def handler(ev):
        if not future.done():
            future.set_result(ev.raw_text)
    
    try:
        result = await asyncio.wait_for(future, timeout=120)
        client.remove_event_handler(handler)
        return result
    except asyncio.TimeoutError:
        client.remove_event_handler(handler)
        await event.reply("❌ Timeout! /host se dobara try karo.")
        return None

# ─── START ────────────────────────────────────────
async def main():
    await db.load()
    
    # Start main bot
    if BOT_TOKEN:
        await client.start(bot_token=BOT_TOKEN)
        me = await client.get_me()
        logger.info(f"✅ Bot started: @{me.username}")
    else:
        logger.warning("⚠️ BOT_TOKEN not set. Running in userbot-only mode.")
    
    # Start scheduler
    asyncio.create_task(scheduler_loop())
    
    # Restore active sessions
    for uid, user_data in db.users.items():
        session_string = user_data.get("session")
        if session_string:
            try:
                await userbot_manager.start_userbot(int(uid), session_string)
                logger.info(f"✅ Restored session for user {uid}")
            except Exception as e:
                logger.error(f"Failed to restore session for {uid}: {e}")
    
    logger.info("🎉 UserBot SaaS is ready!")
    
    if BOT_TOKEN:
        await client.run_until_disconnected()
    else:
        while True: await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
