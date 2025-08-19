# utils/x_since.py
import json
import os

STATE_PATH = "runtime/memory/x_since.json"
os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)

def _load():
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH,"r") as f:
                return json.load(f)
    except: pass
    return {"handles":{}, "keywords":{}}

def _save(d):
    try:
        with open(STATE_PATH,"w") as f:
            json.dump(d,f,indent=2)
    except: pass

def get_since_id(kind: str, key: str) -> str|None:
    d = _load()
    return d.get(kind,{}).get(key)

def set_since_id(kind: str, key: str, since_id: str):
    d = _load()
    d.setdefault(kind,{})[key] = since_id
    _save(d)
