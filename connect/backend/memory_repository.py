"""Mongo is authoritative when configured; local files remain media/processing cache."""
import json
import os
from pathlib import Path


class MemoryRepository:
    def __init__(self, root):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / '.env')
        self.root = root
        uri = os.environ.get('MONGODB_URI')
        self.db = None
        if uri:
            from pymongo import MongoClient
            self.client = MongoClient(uri, serverSelectionTimeoutMS=5000)
            self.client.admin.command('ping')
            self.db = self.client[os.environ.get('MONGODB_DATABASE', 'meridies')]
            self.db.memories.create_index([('modelId', 1), ('floor', 1)])

    def save(self, meta):
        if self.db is None:
            return
        from bson.binary import Binary
        doc = dict(meta)
        doc.update(modelId=meta['building_id'], floor=str(meta.get('floor', '0')))
        path = self.root / meta['audio_file']
        # Deterministic audio id keeps repeated metadata updates from duplicating recordings.
        if path.exists():
            self.db.audio.update_one({'_id': meta['id']}, {'$setOnInsert': {
                'data': Binary(path.read_bytes()), 'mime': meta['audio_type'], 'createdAt': meta['created_at']}}, upsert=True)
        doc['audioId'] = meta['id']
        self.db.memories.replace_one({'_id': meta['id']}, doc, upsert=True)

    def load(self, mid):
        if self.db is None:
            return json.loads((self.root / f'{mid}.json').read_text(encoding='utf-8'))
        meta = self.db.memories.find_one({'_id': mid}, {'_id': 0})
        if meta is None:
            raise FileNotFoundError(mid)
        if hasattr(meta.get('createdAt'), 'isoformat'): meta['createdAt'] = meta['createdAt'].isoformat()
        meta.setdefault('building_id', meta.get('modelId'))
        meta.setdefault('created_at', str(meta.get('createdAt', '')))
        meta.setdefault('audio_file', f'{mid}.webm')
        path = self.root / meta['audio_file']
        if not path.exists():
            aid = meta.get('audioId', mid)
            audio = self.db.audio.find_one({'_id': aid})
            if audio is None and isinstance(aid, str) and len(aid) == 24:
                from bson import ObjectId
                audio = self.db.audio.find_one({'_id': ObjectId(aid)})
            if audio:
                self.root.mkdir(parents=True, exist_ok=True)
                path.write_bytes(bytes(audio['data']))
                meta.setdefault('audio_type', audio.get('mime', 'audio/webm'))
        return meta

    def all(self):
        if self.db is None:
            return [json.loads(p.read_text(encoding='utf-8')) for p in sorted(self.root.glob('*.json')) if len(p.stem) == 32]
        return [self.load(m['_id']) for m in self.db.memories.find({}, {'_id': 1})]

    def migrate(self):
        if self.db is None:
            return 0
        count = 0
        for path in self.root.glob('*.json'):
            if len(path.stem) != 32:
                continue
            if self.db.memories.find_one({'_id': path.stem}, {'_id': 1}) is None:
                self.save(json.loads(path.read_text(encoding='utf-8')))
                count += 1
        return count
