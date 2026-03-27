from pymongo import MongoClient
import os
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# MongoDB connection
MONGODB_URI = os.getenv('MONGODB', '')

class MongoDB:
    _instance = None
    _client = None
    _db = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MongoDB, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._client is None:
            self._connect()
    
    def _connect(self):
        """Initialize MongoDB connection"""
        try:
            if not MONGODB_URI:
                logger.warning("MONGODB environment variable not set. MongoDB features disabled.")
                return
            
            self._client = MongoClient(MONGODB_URI)
            # Test connection
            self._client.admin.command('ping')
            self._db = self._client['landsjobbetter']
            logger.info("✅ MongoDB connected successfully")
        except Exception as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            self._client = None
            self._db = None
    
    def get_db(self):
        """Get database instance"""
        return self._db
    
    def get_collection(self, collection_name):
        """Get a specific collection"""
        if self._db is None:
            return None
        return self._db[collection_name]
    
    def is_connected(self):
        """Check if MongoDB is connected"""
        return self._db is not None

# Singleton instance
mongodb = MongoDB()
