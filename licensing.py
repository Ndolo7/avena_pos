"""Licence check: Ed25519-signed key, bound to this computer's ID. Only the vendor holds the private key."""
import base64, datetime as dt, hashlib, json, os, sys, uuid
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PUBKEY_B64 = "SD525CF2hnU4gQTQCAkyoDOqPajCKwpc-8K7pQmgOxE"
PREFIX = "AVENA1"


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def machine_id():
    raw = ""
    try:
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                raw = winreg.QueryValueEx(k, "MachineGuid")[0]
        elif os.path.exists("/etc/machine-id"):
            raw = open("/etc/machine-id").read().strip()
    except Exception:
        pass
    raw = raw or str(uuid.getnode())
    h = hashlib.sha256(("avena-pos|" + raw).encode()).hexdigest()[:16].upper()
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


def verify(text):
    """Return the licence payload or raise ValueError with a customer-friendly reason."""
    parts = "".join(text.split()).split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise ValueError("That is not a valid license key")
    try:
        payload, sig = _b64d(parts[1]), _b64d(parts[2])
        Ed25519PublicKey.from_public_bytes(_b64d(PUBKEY_B64)).verify(sig, payload)
        p = json.loads(payload)
    except (InvalidSignature, ValueError, KeyError):
        raise ValueError("This license key is not genuine")
    today = dt.date.today()
    if p.get("machine") == "*":  # demo keys: any PC, but must expire within 31 days
        exp, iss = p.get("expires"), p.get("issued")
        if not exp or not iss or (dt.date.fromisoformat(exp) - dt.date.fromisoformat(iss)).days > 31:
            raise ValueError("Invalid demo license")
    elif p.get("machine") != machine_id():
        raise ValueError("This license belongs to a different computer. Send this computer's ID to your provider.")
    if p.get("expires") and today > dt.date.fromisoformat(p["expires"]):
        raise ValueError("This license expired on " + p["expires"])
    return p


def status(get, put, path):
    try:
        text = open(path).read()
    except OSError:
        return {"ok": False, "reason": ""}
    try:
        p = verify(text)
    except ValueError as e:
        return {"ok": False, "reason": str(e)}
    today, last = dt.date.today(), get("last_seen")
    if last and today < dt.date.fromisoformat(last) - dt.timedelta(days=1):
        return {"ok": False, "reason": "The computer's date was set backwards. Correct the date to continue."}
    if not last or today.isoformat() > last:
        put("last_seen", today.isoformat())
    return {"ok": True, "company": p["company"], "modes": p["modes"], "expires": p.get("expires"), "id": p.get("id")}


def activate(text, path):
    verify(text)
    with open(path, "w") as f:
        f.write("".join(text.split()))
