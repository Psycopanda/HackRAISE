import urllib.parse
from motor.motor_asyncio import AsyncIOMotorClient

# Encodage sécurisé du mot de passe
username = "hackathon"
password = urllib.parse.quote_plus("hackathon")
MONGO_URI = f"mongodb+srv://{username}:{password}@cluster0.feewznr.mongodb.net/"

client = AsyncIOMotorClient(MONGO_URI)
db = client.vibecode_db

# Collections
sessions_collection = db.sessions
files_collection = db.files
messages_collection = db.messages