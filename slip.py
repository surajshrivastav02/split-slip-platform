"""Vercel entry point: /api/slip?action=... . All money is integer paise."""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS ss_users(phone TEXT PRIMARY KEY, name TEXT NOT NULL, upi_phone TEXT NOT NULL, salt TEXT NOT NULL, digest TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ss_sessions(token TEXT PRIMARY KEY, phone TEXT NOT NULL REFERENCES ss_users(phone), expires BIGINT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ss_groups(id TEXT PRIMARY KEY, owner TEXT NOT NULL, rev INTEGER NOT NULL, ledger TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ss_members(group_id TEXT REFERENCES ss_groups(id), phone TEXT REFERENCES ss_users(phone), PRIMARY KEY(group_id,phone))",
    "CREATE INDEX IF NOT EXISTS ss_members_phone ON ss_members(phone)",
    "CREATE TABLE IF NOT EXISTS ss_invites(token TEXT PRIMARY KEY, group_id TEXT REFERENCES ss_groups(id), phone TEXT NOT NULL, expires BIGINT NOT NULL, UNIQUE(group_id,phone))",
    "CREATE TABLE IF NOT EXISTS ss_bills(id TEXT PRIMARY KEY, group_id TEXT REFERENCES ss_groups(id), content TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ss_limits(bucket TEXT PRIMARY KEY, hits INTEGER NOT NULL, expires BIGINT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ss_checks(token TEXT PRIMARY KEY, answer TEXT NOT NULL, expires BIGINT NOT NULL)"
]
MAX_MEMBERS = 12
MAX_LEDGER_BYTES = 850000
ready = False

class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message

def require(condition, message, status=400):
    if not condition:
        raise Problem(status, message)

def text(value, length=100):
    require(isinstance(value, str) and 0 < len(value.strip()) <= length, "Check the text fields.")
    return value.strip()

def phone(value):
    require(isinstance(value, str) and re.fullmatch(r"[6-9][0-9]{9}", value), "Enter a valid 10-digit mobile number.")
    return value

def index(value, items):
    require(type(value) is int and 0 <= value < len(items), "Invalid member or expense.")
    return value

def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()

def stamp():
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def database():
    """Only the explicit local runner can opt in to SQLite."""
    global ready
    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    local = not os.environ.get("VERCEL") and os.environ.get("SPLIT_LOCAL_DB") and not url
    if local:
        import sqlite3
        conn = sqlite3.connect(os.environ["SPLIT_LOCAL_DB"])
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
    else:
        require(url, "Database setup needed: connect Neon, set DATABASE_URL, then redeploy.", 503)
        import psycopg
        conn = psycopg.connect(url, connect_timeout=8, prepare_threshold=None)
    def sql(statement, params=()):
        return conn.execute(statement if local else statement.replace("?", "%s"), params)
    try:
        if not ready:
            if not local:
                sql("SELECT pg_advisory_xact_lock(583120421)")
            for statement in SCHEMA:
                sql(statement)
        yield sql
        conn.commit()
        ready = True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def throttle(db, key, limit, period):
    now = int(time.time())
    bucket = token_hash(key + ":" + str(now // period))
    hits = db("INSERT INTO ss_limits(bucket,hits,expires) VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET hits=ss_limits.hits+1 RETURNING hits",
              (bucket, now + period)).fetchone()[0]
    require(hits <= limit, "Too many attempts. Please try later.", 429)
    if secrets.randbelow(40) == 0:
        for table in ["ss_limits", "ss_checks", "ss_invites", "ss_sessions"]:
            db("DELETE FROM " + table + " WHERE expires < ?", (now,))

def add_group(db, user, title):
    gid = secrets.token_hex(12)
    member = {"phone":user[0], "name":user[1], "upi":""}
    ledger = {"name":text(title,60), "members":[member], "expenses":[], "payments":[]}
    db("INSERT INTO ss_groups VALUES(?,?,0,?)", (gid,user[0],json.dumps(ledger)))
    db("INSERT INTO ss_members VALUES(?,?)", (gid,user[0]))
    return gid

def profile(db, sid):
    row = db("SELECT u.phone,u.name FROM ss_users u JOIN ss_sessions s ON s.phone=u.phone WHERE s.token=? AND s.expires>?",
             (token_hash(sid),int(time.time()))).fetchone()
    require(row, "Please log in.", 401)
    return row

def read_group(db, gid, user):
    row = db("SELECT g.owner,g.rev,g.ledger FROM ss_groups g JOIN ss_members m ON m.group_id=g.id WHERE g.id=? AND m.phone=?",
             (gid,user[0])).fetchone()
    require(row, "Group unavailable.", 404)
    return row[0], row[1], json.loads(row[2])

def store_bill(db, gid, bill):
    if bill is None:
        return None
    require(isinstance(bill, dict), "Invalid receipt.")
    name = text(bill.get("name"), 180)
    value = text(bill.get("data"), 1350000)
    head, sep, encoded = value.partition(",")
    signatures = {"data:image/png;base64":b"\x89PNG\r\n\x1a\n", "data:image/jpeg;base64":b"\xff\xd8\xff", "data:application/pdf;base64":b"%PDF-"}
    require(sep and head in signatures, "Choose a PNG, JPEG or PDF receipt.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise Problem(400, "Invalid receipt encoding.")
    require(0 < len(raw) <= 1000000 and raw.startswith(signatures[head]), "Invalid file format or receipt exceeds 1 MB.")
    bid = secrets.token_hex(12)
    db("INSERT INTO ss_bills VALUES(?,?,?)", (bid,gid,value))
    return {"id":bid,"name":name}

def net_balances(g):
    net = [0] * len(g["members"])
    for e in g["expenses"]:
        net[e["payer"]] += e["amount"]
        for i, amount in e["shares"].items():
            net[int(i)] -= amount
    for p in g["payments"]:
        net[p["from"]] += p["amount"]
        net[p["to"]] -= p["amount"]
    return net

def change(db, action, d, user):
    gid = text(d.get("group"))
    owner, rev, g = read_group(db, gid, user)
    require(d.get("revision") == rev, "Another member updated this group. Refresh before retrying.", 409)
    people = g["members"]
    extra = {}
    if action == "member":
        i = d.get("index")
        if i is None:
            require(owner == user[0], "Only the group creator can add members.", 403)
            require(len(people) < MAX_MEMBERS, "This small-group edition supports 12 members.")
            number = phone(d.get("phone"))
            require(all(m["phone"] != number for m in people), "This phone number is already in the group.")
            people.append({"phone":number,"name":text(d.get("name")),"upi":""})
            i = len(people)-1
        m = people[index(i,people)]
        require(m["phone"] == user[0] or owner == user[0], "You can only edit your own profile.", 403)
        require(d.get("phone") == m["phone"], "Member phone numbers cannot be changed.")
        upi = d.get("upi","")
        require(isinstance(upi,str) and (not upi or re.fullmatch(r"[A-Za-z0-9._-]{2,120}@[A-Za-z0-9]{2,40}",upi)), "Check the UPI ID.")
        m.update(name=text(d.get("name")),upi=upi)
    elif action == "invite":
        require(owner == user[0], "Only the group creator can invite members.", 403)
        number = people[index(d.get("index"),people)]["phone"]
        require(not db("SELECT 1 FROM ss_members WHERE group_id=? AND phone=?", (gid,number)).fetchone(), "Already joined.")
        code = secrets.token_urlsafe(18)
        db("DELETE FROM ss_invites WHERE group_id=? AND phone=?", (gid,number))
        db("INSERT INTO ss_invites VALUES(?,?,?,?)",(token_hash(code),gid,number,int(time.time())+86400))
        extra["code"] = code
    elif action == "expense":
        category = d.get("cat")
        require(category in ("Food","Travel","Electricity","E-commerce","Other"),"Choose a category.")
        amount = d.get("amount")
        require(type(amount) is int and 0 < amount <= 999999900, "Check the amount.")
        payer = index(d.get("payer"),people)
        shares = d.get("shares")
        require(isinstance(shares,dict) and shares and all(k in {str(i) for i in range(len(people))} and type(v) is int and v>=0 for k,v in shares.items()) and sum(shares.values())==amount, "Shares must equal the expense total.")
        date = text(d.get("date"),40)
        try:
            require(datetime.fromisoformat(date.replace("Z","+00:00")).tzinfo, "Choose a date with a timezone.")
        except ValueError:
            raise Problem(400,"Invalid expense date.")
        bill = store_bill(db,gid,d.get("bill"))
        require(bill or category not in ("Electricity","E-commerce"),"A bill is required for this category.")
        g["expenses"].append(dict(title=text(d.get("title")),cat=category,amount=amount,payer=payer,shares=shares,date=date,bill=bill,comments=[]))
    elif action == "comment":
        expense = g["expenses"][index(d.get("index"),g["expenses"])]
        expense["comments"].append({"author":user[1],"text":text(d.get("text"),600),"date":stamp()})
    elif action == "settle":
        sender,receiver = index(d.get("from"),people),index(d.get("to"),people)
        require(user[0] in (people[sender]["phone"],people[receiver]["phone"]), "Only a party to this payment can confirm it.", 403)
        net = net_balances(g)
        amount = d.get("amount")
        require(type(amount) is int and 0<amount<=min(-net[sender],net[receiver]), "Balances changed. Refresh the settlement plan.",409)
        g["payments"].append({"from":sender,"to":receiver,"amount":amount,"date":stamp()})
    encoded = json.dumps(g,ensure_ascii=False)
    require(len(encoded.encode()) <= MAX_LEDGER_BYTES, "This group sheet is full. Start a new group.")
    updated = db("UPDATE ss_groups SET ledger=?,rev=rev+1 WHERE id=? AND rev=?",(encoded,gid,rev)).rowcount
    require(updated == 1,"Group changed. Refresh and try again.",409)
    return extra

class handler(BaseHTTPRequestHandler):
    def send_json(self, status, data, cookie=None):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","private, no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        if cookie:
            self.send_header("Set-Cookie",cookie)
        self.end_headers()
        self.wfile.write(body)

    def run(self):
        query = parse_qs(urlsplit(self.path).query)
        action = query.get("action",[""])[0]
        post = self.command=="POST"
        cookie = SimpleCookie(self.headers.get("Cookie",""))
        sid = cookie["ss_session"].value if "ss_session" in cookie else ""
        local = not os.environ.get("VERCEL") and os.environ.get("SPLIT_LOCAL_DB")
        attrs = "; Path=/; HttpOnly; SameSite=Lax" + ("" if local else "; Secure")
        set_cookie = None
        d = {}
        if post:
            origin = urlsplit(self.headers.get("Origin",""))
            require(origin.netloc and origin.netloc==self.headers.get("Host") and origin.scheme in ("https","http"), "Open this form from the same site.",403)
            require(self.headers.get_content_type()=="application/json","Use JSON.",415)
            size = int(self.headers.get("Content-Length",0))
            require(0<size<=1400000,"Request exceeds limit.",413)
            d = json.loads(self.rfile.read(size))
            require(isinstance(d,dict),"Invalid request.")
        else:
            require(action in ("state","group","bill"), "Use the correct request method.",405)
        if post and action in ("challenge","signup","login"):
            # Commit counters and consume the one-use challenge even if credentials fail.
            ip = self.headers.get("x-vercel-forwarded-for",self.client_address[0]).split(",")[0]
            with database() as db:
                throttle(db,action+ip,100 if action=="challenge" else 30,600)
                if action != "challenge":
                    throttle(db,"attempt:"+str(d.get("phone",""))[:16],20,600)
                    check=db("DELETE FROM ss_checks WHERE token=? AND expires>? RETURNING answer",
                             (token_hash(text(d.get("challenge"),100)),int(time.time()))).fetchone()
            if action!="challenge":
                require(check and check[0]==str(d.get("answer")), "Human check expired or incorrect. Try the new question.")
        with database() as db:
            if action=="challenge":
                a,b=secrets.randbelow(9)+1,secrets.randbelow(9)+1
                token=secrets.token_urlsafe(24)
                db("INSERT INTO ss_checks VALUES(?,?,?)",(token_hash(token),str(a+b),int(time.time())+300))
                out={"challenge":token,"question":f"{a} + {b} = ?"}
            elif action in ("signup","login"):
                number=phone(d.get("phone"))
                pw=d.get("password")
                require(isinstance(pw,str) and 1<=len(pw)<=256,"Enter a valid password.")
                if action=="signup":
                    require(len(pw)>=8,"Use at least 8 characters.")
                    name=text(d.get("name"),80)
                    upi=d.get("upi") or ""
                    if upi: phone(upi)
                    salt=secrets.token_hex(16)
                    created=db("INSERT INTO ss_users VALUES(?,?,?,?,?) ON CONFLICT(phone) DO NOTHING",
                            (number,name,upi,salt,password_hash(pw,salt))).rowcount
                    require(created,"Account unavailable. Try logging in.",409)
                    add_group(db,(number,name),"Hostel Buddies")
                else:
                    row=db("SELECT salt,digest FROM ss_users WHERE phone=?",(number,)).fetchone()
                    digest=password_hash(pw,row[0] if row else "00"*16)
                    require(row and hmac.compare_digest(row[1],digest),"Incorrect phone number or password.",401)
                token=secrets.token_urlsafe(32)
                db("DELETE FROM ss_sessions WHERE token=?",(token_hash(sid),))
                db("INSERT INTO ss_sessions VALUES(?,?,?)",(token_hash(token),number,int(time.time())+28800))
                set_cookie="ss_session="+token+"; Max-Age=28800"+attrs
                out={"ok":True}
            else:
                user=profile(db,sid)
                if action=="state":
                    rows=db("SELECT g.id,g.ledger FROM ss_groups g JOIN ss_members m ON g.id=m.group_id WHERE m.phone=? ORDER BY g.id",(user[0],)).fetchall()
                    out={"me":user[0],"groups":[{"id":r[0],"name":json.loads(r[1])["name"]} for r in rows]}
                elif action in ("group","bill"):
                    gid=query.get("group",[""])[0]
                    owner,rev,g=read_group(db,gid,user)
                    if action=="group":
                        out=dict(g,owner=owner,revision=rev,id=gid)
                    else:
                        row=db("SELECT content FROM ss_bills WHERE group_id=? AND id=?",(gid,query.get("id",[""])[0])).fetchone()
                        require(row,"Receipt unavailable.",404)
                        out={"data":row[0]}
                elif action=="logout":
                    db("DELETE FROM ss_sessions WHERE token=?",(token_hash(sid),))
                    set_cookie="ss_session=; Max-Age=0"+attrs
                    out={"ok":True}
                elif action=="create":
                    throttle(db,"group:"+user[0],15,3600)
                    out={"id":add_group(db,user,text(d.get("name"),60))}
                elif action=="join":
                    code=token_hash(text(d.get("code"),100))
                    row=db("DELETE FROM ss_invites WHERE token=? AND phone=? AND expires>? RETURNING group_id",(code,user[0],int(time.time()))).fetchone()
                    require(row,"Invite invalid, expired, or issued for another phone number.")
                    db("INSERT INTO ss_members VALUES(?,?) ON CONFLICT DO NOTHING",(row[0],user[0]))
                    out={"id":row[0]}
                elif action in ("member","invite","expense","comment","settle"):
                    out=change(db,action,d,user)
                else:
                    raise Problem(404,"Unknown action.")
        self.send_json(200,out,set_cookie)

    def dispatch(self):
        try:
            self.run()
        except Problem as exc:
            self.send_json(exc.status,{"error":exc.message})
        except (ValueError,TypeError,UnicodeError):
            self.send_json(400,{"error":"Invalid input."})
        except Exception as exc:
            logging.error("Split Slip backend error: %s",type(exc).__name__)
            self.send_json(503,{"error":"Database unavailable. Check the project database connection and retry."})

    do_GET = do_POST = dispatch
