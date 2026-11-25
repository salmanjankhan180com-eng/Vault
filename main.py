import os, json, hashlib, time, threading, queue, shutil, zipfile, base64, mimetypes, subprocess, math
try:
    import ffpyplayer  # noqa: F401
    os.environ.setdefault("KIVY_VIDEO", "ffpyplayer")
    HAS_FFPY, FFPY_ERR = True, ""
except Exception as _e:
    HAS_FFPY, FFPY_ERR = False, str(_e)[:80]
from kivy.app import App
from kivy.cache import Cache
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle, Rectangle, Ellipse, Line, Triangle, Mesh
from kivy.metrics import dp
from kivy.utils import get_color_from_hex as hx
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.button import Button
from kivy.uix.label import Label as KLabel
from kivy.uix.widget import Widget
from kivy.uix.image import Image, AsyncImage
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition, NoTransition

try:
    from kivy.uix.video import Video
except Exception:
    Video = None

try:
    from PIL import Image as PILImage
except Exception:
    PILImage = None

# ---------------- Theme ----------------
RED, GREEN = "#E5484D", "#2FB26B"
BG = ACCENT = CARD = SOFT = MUTED = NAVBG = HEAD = TXT = "#000000"
# (pastel colour, accent colour)
SCHEMES = [("#B5D0F8", "#1A6DE0"), ("#EBDDF1", "#8E44AD"), ("#DFEEDD", "#2E8B57"),
           ("#F2EBE1", "#B7791F"), ("#E0F1EE", "#12998A"), ("#E0E7F1", "#4A6FA5"),
           ("#F3DEDE", "#D6455D"), ("#EDF1E0", "#7A9A1E")]


def mix(h1, h2, t):
    a, b = hx(h1), hx(h2)
    c = [a[i] * (1 - t) + b[i] * t for i in range(3)]
    return "#%02X%02X%02X" % tuple(int(x * 255) for x in c)


def apply_theme(mode, scheme):
    global BG, ACCENT, CARD, SOFT, MUTED, NAVBG, HEAD, TXT
    pastel, acc = SCHEMES[scheme if 0 <= scheme < len(SCHEMES) else 0]
    hr = time.localtime().tm_hour
    night = mode == "night" or (mode == "auto" and (hr >= 19 or hr < 6))
    if night:
        BG, CARD, SOFT, NAVBG, HEAD = "#16171B", "#272A33", "#343845", "#1E2026", "#1E2026"
        TXT, MUTED, ACCENT = "#F2F2F7", "#9A9AB0", mix(acc, "#FFFFFF", .15)
    else:
        BG, CARD, SOFT = "#F8FCFF", mix(pastel, "#FFFFFF", .45), mix(pastel, "#000000", .08)
        NAVBG, HEAD, TXT, MUTED, ACCENT = pastel, pastel, "#101114", "#6A707C", acc
    Window.clearcolor = hx(BG)


# ---------------- Constants ----------------
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
VID_EXTS = (".mp4", ".mkv", ".3gp", ".mov", ".avi", ".webm")
ALL_EXTS = IMG_EXTS + VID_EXTS
ROOT = "/storage/emulated/0"
RESTORE_DIR = "/storage/emulated/0/Pictures/VaultRestored"
PLAY_DIR = "/storage/emulated/0/.vault_play"
AUTOLOCK_OPTS = [("Off", 0), ("15 sec", 15), ("30 sec", 30), ("1 min", 60), ("5 min", 300)]
MAGIC = b"PVT1"
CHUNK = 1 << 18
MIN_PIN, MAX_PIN = 4, 12
DEFAULT_FOLDERS = ("Pictures", "Downloads", "Videos")
TRASH_DAYS = 30
# key, label, columns, cell height (dp), description
FOLDER_STYLES = [
    ("default", "Default", 2, 170, "Big folder icons, 2 per row"),
    ("standard", "Standard", 3, 135, "Small folder icons, 3 per row"),
    ("large", "Large Cover", 2, 190, "Cover photo with name below, 2 per row"),
    ("mini", "Mini Cover", 2, 64, "Small cover on the left, 2 per row"),
    ("list", "List View", 1, 92, "One folder per row with cover"),
    ("grid", "Grid View", 3, 150, "Cover photo grid, 3 per row"),
    ("xlarge", "Extra Large Cover", 1, 230, "One big cover per row"),
]
LANGS = [("en", "English"), ("hi", "Hindi"), ("mr", "Marathi"), ("bn", "Bengali"),
         ("cs", "Cestina"), ("it", "Italiano"), ("es", "Espanol"), ("pt", "Portugues"),
         ("de", "Deutsch"), ("eu", "Euskara"), ("fr", "Francais"), ("ar", "Arabic"),
         ("fa", "Persian"), ("ja", "Japanese"), ("kn", "Kannada"), ("ko", "Korean"),
         ("ml", "Malayalam"), ("pl", "Polski"), ("ru", "Russian"), ("ta", "Tamil"),
         ("tr", "Turkce"), ("uk", "Ukrainian")]
AVAILABLE_LANGS = {"en"}


def ask_android_permissions():
    try:
        from android.permissions import request_permissions
        request_permissions([
            "android.permission.READ_MEDIA_IMAGES",
            "android.permission.READ_MEDIA_VIDEO",
            "android.permission.READ_EXTERNAL_STORAGE",
            "android.permission.WRITE_EXTERNAL_STORAGE",
        ])
    except Exception:
        pass


PICK_CODE = 7701
MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
            "image/gif": ".gif", "image/bmp": ".bmp", "video/mp4": ".mp4",
            "video/x-matroska": ".mkv", "video/3gpp": ".3gp", "video/quicktime": ".mov",
            "video/x-msvideo": ".avi", "video/webm": ".webm"}


PICK_DBG = ""


def open_gallery_picker(on_result, kind="photo", mode="gallery"):
    """Opens the phone's gallery (mode='gallery') or the Files picker (mode='document').
    Returns True if it started. PICK_DBG keeps a short reason when something fails."""
    global PICK_DBG
    PICK_DBG = ""
    try:
        from android import activity
        from jnius import autoclass
        Intent = autoclass("android.content.Intent")
        Build = autoclass("android.os.Build$VERSION")
        MediaStore = autoclass("android.provider.MediaStore")
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        mime = "video/*" if kind == "video" else "image/*"

        if mode == "document":
            it = Intent(Intent.ACTION_OPEN_DOCUMENT)
            it.setType(mime)
            it.addCategory(Intent.CATEGORY_OPENABLE)
            it.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, True)
        elif Build.SDK_INT >= 33:
            it = Intent("android.provider.action.PICK_IMAGES")
            it.setType(mime)
            it.putExtra("android.provider.extra.PICK_IMAGES_MAX",
                        int(MediaStore.getPickImagesMaxLimit()))
        else:
            if kind == "video":
                coll = autoclass("android.provider.MediaStore$Video$Media").EXTERNAL_CONTENT_URI
            else:
                coll = autoclass("android.provider.MediaStore$Images$Media").EXTERNAL_CONTENT_URI
            it = Intent(Intent.ACTION_PICK, coll)
            it.setType(mime)
            it.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, True)

        def cb(req, res, data):
            global PICK_DBG
            if req != PICK_CODE:
                return
            try:
                activity.unbind(on_activity_result=cb)
            except Exception:
                pass
            uris = []
            PICK_DBG = "result=%s data=%s" % (res, "yes" if data is not None else "no")
            try:
                if res == -1 and data is not None:
                    clip = data.getClipData()
                    if clip is not None:
                        for i in range(clip.getItemCount()):
                            uris.append(str(clip.getItemAt(i).getUri().toString()))
                    elif data.getData() is not None:
                        uris.append(str(data.getData().toString()))
            except Exception as e:
                PICK_DBG += " read error: " + str(e)[:60]
                uris = []
            on_result(uris)

        activity.bind(on_activity_result=cb)
        act.startActivityForResult(it, PICK_CODE)
        return True
    except Exception as e:
        PICK_DBG = "start error: " + str(e)[:100]
        return False


def copy_uri_to_file(uri_str, dst_dir, i):
    from jnius import autoclass
    Uri = autoclass("android.net.Uri")
    act = autoclass("org.kivy.android.PythonActivity").mActivity
    cr = act.getContentResolver()
    uri = Uri.parse(uri_str)
    mime = str(cr.getType(uri) or "").lower()
    ext = MIME_EXT.get(mime) or (".mp4" if mime.startswith("video") else ".jpg")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, f"pick_{int(time.time() * 1000)}_{i}{ext}")
    pfd = cr.openFileDescriptor(uri, "r")
    fd = pfd.detachFd()
    try:
        with os.fdopen(fd, "rb") as fi, open(dst, "wb") as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
    except Exception:
        _rm(dst)
        raise
    return dst


DELETE_CODE = 7704


def _media_uri(uri_str):
    """Converts a picker / document uri into a MediaStore uri (None if not possible)."""
    from jnius import autoclass
    Uri = autoclass("android.net.Uri")
    ContentUris = autoclass("android.content.ContentUris")
    cr = autoclass("org.kivy.android.PythonActivity").mActivity.getContentResolver()
    u = Uri.parse(uri_str)
    auth = str(u.getAuthority() or "")
    path = str(u.getPath() or "")
    seg = str(u.getLastPathSegment() or "")
    vid = str(cr.getType(u) or "").lower().startswith("video")
    mid = None
    if auth == "media":
        if "picker" in path:
            mid = seg
        elif "/media/" in path:
            return u
    elif auth == "com.android.providers.media.documents" and ":" in seg:
        kind, mid = seg.split(":", 1)
        vid = kind == "video"
    if not mid or not str(mid).isdigit():
        return None
    coll = autoclass("android.provider.MediaStore$Video$Media" if vid
                     else "android.provider.MediaStore$Images$Media").EXTERNAL_CONTENT_URI
    return ContentUris.withAppendedId(coll, int(mid))


def delete_from_gallery(uris, on_done):
    """Deletes the original files from the phone Gallery after they were imported.
    Android 11+ shows its own 'Allow to delete?' box once. on_done(True/False).
    Returns False if it could not even start."""
    try:
        from android import activity
        from jnius import autoclass
        Build = autoclass("android.os.Build$VERSION")
        MediaStore = autoclass("android.provider.MediaStore")
        ArrayList = autoclass("java.util.ArrayList")
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        cr = act.getContentResolver()
        murs = []
        for u in uris:
            try:
                m = _media_uri(u)
                if m is not None:
                    murs.append(m)
            except Exception:
                pass
        if not murs:
            return False
        if Build.SDK_INT >= 30:
            lst = ArrayList()
            for m in murs:
                lst.add(m)
            pi = MediaStore.createDeleteRequest(cr, lst)

            def cb(req, res, data):
                if req != DELETE_CODE:
                    return
                try:
                    activity.unbind(on_activity_result=cb)
                except Exception:
                    pass
                on_done(res == -1)

            activity.bind(on_activity_result=cb)
            act.startIntentSenderForResult(pi.getIntentSender(), DELETE_CODE, None, 0, 0, 0)
            return True
        n = 0
        for m in murs:
            try:
                n += int(cr.delete(m, None, None))
            except Exception:
                pass
        on_done(n >= len(murs))
        return True
    except Exception:
        return False


def media_rescan(paths):
    """Tells the Gallery that files were removed, so they stop showing."""
    try:
        from jnius import autoclass
        MSC = autoclass("android.media.MediaScannerConnection")
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        MSC.scanFile(act, [str(p) for p in paths], None, None)
    except Exception:
        pass


BACKUP_CODE, OPEN_CODE = 7702, 7703


def android_document(action, code, on_result, filename=None):
    try:
        from android import activity
        from jnius import autoclass
        Intent = autoclass("android.content.Intent")
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        if action == "create":
            it = Intent(Intent.ACTION_CREATE_DOCUMENT)
            it.setType("application/zip")
            it.putExtra(Intent.EXTRA_TITLE, filename or "VaultBackup.zip")
        else:
            it = Intent(Intent.ACTION_OPEN_DOCUMENT)
            it.setType("*/*")
        it.addCategory(Intent.CATEGORY_OPENABLE)

        def cb(req, res, data):
            if req != code:
                return
            try:
                activity.unbind(on_activity_result=cb)
            except Exception:
                pass
            uri = None
            try:
                if res == -1 and data is not None and data.getData() is not None:
                    uri = str(data.getData().toString())
            except Exception:
                uri = None
            on_result(uri)

        activity.bind(on_activity_result=cb)
        act.startActivityForResult(it, code)
        return True
    except Exception:
        return False


def copy_path_to_uri(src, uri_str):
    from jnius import autoclass
    Uri = autoclass("android.net.Uri")
    cr = autoclass("org.kivy.android.PythonActivity").mActivity.getContentResolver()
    pfd = cr.openFileDescriptor(Uri.parse(uri_str), "w")
    fd = pfd.detachFd()
    with os.fdopen(fd, "wb") as fo, open(src, "rb") as fi:
        shutil.copyfileobj(fi, fo, 1 << 20)


def copy_uri_to_path(uri_str, dst):
    from jnius import autoclass
    Uri = autoclass("android.net.Uri")
    cr = autoclass("org.kivy.android.PythonActivity").mActivity.getContentResolver()
    pfd = cr.openFileDescriptor(Uri.parse(uri_str), "r")
    fd = pfd.detachFd()
    try:
        with os.fdopen(fd, "rb") as fi, open(dst, "wb") as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
    except Exception:
        _rm(dst)
        raise


def export_to_gallery(src, fname):
    """Puts a decrypted file back in the phone Gallery (MediaStore on Android 10+,
    otherwise Pictures/VaultRestored)."""
    try:
        from jnius import autoclass
        Build = autoclass("android.os.Build$VERSION")
        if Build.SDK_INT >= 29:
            ContentValues = autoclass("android.content.ContentValues")
            cr = autoclass("org.kivy.android.PythonActivity").mActivity.getContentResolver()
            vid = is_video(fname)
            mime = mimetypes.guess_type(fname)[0] or ("video/mp4" if vid else "image/jpeg")
            rel = ("Movies" if vid else "Pictures") + "/VaultRestored"
            vals = ContentValues()
            vals.put("_display_name", fname)
            vals.put("mime_type", mime)
            vals.put("relative_path", rel)
            coll = autoclass("android.provider.MediaStore$Video$Media" if vid
                             else "android.provider.MediaStore$Images$Media").EXTERNAL_CONTENT_URI
            uri = cr.insert(coll, vals)
            if uri is None:
                raise Exception("MediaStore insert failed")
            pfd = cr.openFileDescriptor(uri, "w")
            fd = pfd.detachFd()
            with os.fdopen(fd, "wb") as fo, open(src, "rb") as fi:
                shutil.copyfileobj(fi, fo, 1 << 20)
            return rel + "/" + fname
    except Exception:
        pass
    os.makedirs(RESTORE_DIR, exist_ok=True)
    stem, ext = os.path.splitext(fname)
    dest = os.path.join(RESTORE_DIR, stem + ext)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(RESTORE_DIR, f"{stem}_{n}{ext}")
        n += 1
    shutil.copyfile(src, dest)
    return dest


def base_name(n):
    return n[:-6] if n.endswith(".vault") else n


def is_video(name):
    return base_name(name).lower().endswith(VID_EXTS)


def valid_pin(p):
    return p.isdigit() and MIN_PIN <= len(p) <= MAX_PIN


# ---------------- External video player ----------------
def clear_play_dir():
    """Deletes temporary decrypted videos that were opened in the phone's video player."""
    try:
        for f in os.listdir(PLAY_DIR):
            if f != ".nomedia":
                _rm(os.path.join(PLAY_DIR, f))
    except Exception:
        pass


def video_provider_ok():
    """True only when a real video engine (ffpyplayer) is installed."""
    return HAS_FFPY and Video is not None


def play_external(path):
    """Opens a video file in the phone's own video player. Returns (ok, error_text)."""
    mime = mimetypes.guess_type(path)[0] or "video/*"
    uri = "file://" + path
    # 1) Android APK: direct intent
    try:
        from jnius import autoclass
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        StrictMode = autoclass("android.os.StrictMode")
        VmPolicy = autoclass("android.os.StrictMode$VmPolicy$Builder")
        StrictMode.setVmPolicy(VmPolicy().build())
        it = Intent(Intent.ACTION_VIEW)
        it.setDataAndType(Uri.parse(uri), mime)
        act.startActivity(it)
        return True, ""
    except Exception:
        pass
    # 2) Pydroid: Android 'am' command
    last = ""
    for exe in ("am", "/system/bin/am"):
        try:
            r = subprocess.run([exe, "start", "-a", "android.intent.action.VIEW",
                                "-d", uri, "-t", mime],
                               capture_output=True, text=True, timeout=10)
            out = (r.stdout or "") + (r.stderr or "")
            if r.returncode == 0 and "Error" not in out and "Exception" not in out:
                return True, ""
            last = out.strip()[:120] or "player did not start"
        except Exception as e:
            last = str(e)
    return False, last


# ---------------- Crypto helpers ----------------
def _ks(key, nonce, idx, n):
    return hashlib.shake_256(key + nonce + idx.to_bytes(8, "big")).digest(n)


def _xor(data, ks):
    n = len(data)
    if n == 0:
        return b""
    return (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(n, "big")


def _xor_bytes(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def _rm(path):
    try:
        os.remove(path)
    except Exception:
        pass


def make_video_thumb(src, tp):
    """Takes one frame of a video and saves it as a small JPEG. Returns path or None."""
    try:
        from jnius import autoclass
        MMR = autoclass("android.media.MediaMetadataRetriever")
        Bitmap = autoclass("android.graphics.Bitmap")
        Fmt = autoclass("android.graphics.Bitmap$CompressFormat")
        FOS = autoclass("java.io.FileOutputStream")
        r = MMR()
        r.setDataSource(src)
        bmp = r.getFrameAtTime(1000000)
        if bmp is None:
            bmp = r.getFrameAtTime()
        r.release()
        if bmp is None:
            return None
        w, h = bmp.getWidth(), bmp.getHeight()
        sc = 360.0 / max(w, h)
        bmp = Bitmap.createScaledBitmap(bmp, max(1, int(w * sc)), max(1, int(h * sc)), True)
        fos = FOS(tp)
        bmp.compress(Fmt.JPEG, 80, fos)
        fos.close()
        return tp
    except Exception:
        return None


# ---------------- Storage ----------------
class Store:
    def __init__(self, base, cache):
        self.media = os.path.join(base, "media")
        self.trash = os.path.join(base, "trash")
        self.cache = cache
        for d in (self.media, self.trash, self.cache):
            os.makedirs(d, exist_ok=True)
        self.key = None
        self.clear_cache()
        self.cfg_path = os.path.join(base, "config.json")
        self.cfg = {"pin": None, "albums": {}, "album_kind": {}, "trash": {},
                    "autolock": 60, "fails": 0, "lock_until": 0,
                    "theme": "light", "scheme": 0, "folder_style": "default", "lang": "en"}
        try:
            with open(self.cfg_path) as f:
                self.cfg.update(json.load(f))
        except Exception:
            pass

    def save(self):
        tmp = self.cfg_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.cfg, f)
        os.replace(tmp, self.cfg_path)

    # ----- keys / PIN -----
    @staticmethod
    def h(p):
        return hashlib.sha256(p.encode()).hexdigest()

    @staticmethod
    def _pk(pin, salt):
        return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 50000, 32)

    @staticmethod
    def _mask(pk):
        return hashlib.shake_256(pk + b"wrap").digest(32)

    def has_keys(self):
        return "wrapped" in self.cfg

    def is_legacy(self):
        return bool(self.cfg.get("pin")) and not self.has_keys()

    def check_legacy(self, pin):
        return self.cfg.get("pin") == self.h(pin)

    def setup_keys(self, pin, master=None):
        master = master or os.urandom(32)
        salt = os.urandom(16)
        pk = self._pk(pin, salt)
        self.cfg["salt"] = salt.hex()
        self.cfg["wrapped"] = _xor_bytes(master, self._mask(pk)).hex()
        self.cfg["check"] = hashlib.sha256(master).hexdigest()
        self.cfg.pop("pin", None)
        self.save()
        self.key = master

    def unlock(self, pin):
        if not self.has_keys():
            return None
        pk = self._pk(pin, bytes.fromhex(self.cfg["salt"]))
        master = _xor_bytes(bytes.fromhex(self.cfg["wrapped"]), self._mask(pk))
        if hashlib.sha256(master).hexdigest() == self.cfg["check"]:
            return master
        return None

    def rewrap(self, master, newpin):
        salt = os.urandom(16)
        pk = self._pk(newpin, salt)
        self.cfg["salt"] = salt.hex()
        self.cfg["wrapped"] = _xor_bytes(master, self._mask(pk)).hex()
        self.save()

    # ----- recovery key / backup / import -----
    def recovery_key(self):
        if self.key is None:
            raise Exception("Vault locked")
        s = base64.b32encode(self.key).decode().rstrip("=")
        return "-".join(s[i:i + 4] for i in range(0, len(s), 4))

    @staticmethod
    def parse_recovery_key(text):
        s = "".join(c for c in (text or "").upper() if c.isalnum())
        try:
            s += "=" * (-len(s) % 8)
            m = base64.b32decode(s)
        except Exception:
            return None
        return m if len(m) == 32 else None

    def master_ok(self, master, cfg=None):
        cfg = cfg if cfg is not None else self.cfg
        return bool(master) and "check" in cfg and \
            hashlib.sha256(master).hexdigest() == cfg["check"]

    def recover_with_key(self, master, newpin):
        if not self.has_keys() or not self.master_ok(master):
            return False
        self.rewrap(master, newpin)
        self.key = master
        self.cfg["fails"], self.cfg["lock_until"] = 0, 0
        self.save()
        return True

    def make_backup(self, dest, cb=None):
        names = [f for f in os.listdir(self.media) if f.endswith(".vault")]
        meta = {"v": 1, "check": self.cfg.get("check"),
                "albums": self.cfg.get("albums", {}),
                "album_kind": self.cfg.get("album_kind", {}),
                "autolock": self.cfg.get("autolock", 60)}
        tmp = dest + ".part"
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
                z.writestr("config.json", json.dumps(meta))
                for i, n in enumerate(names):
                    z.write(os.path.join(self.media, n), "media/" + n)
                    if cb:
                        cb(i + 1, len(names))
            os.replace(tmp, dest)
        except Exception:
            _rm(tmp)
            raise
        return len(names)

    @staticmethod
    def read_backup(zpath):
        try:
            with zipfile.ZipFile(zpath) as z:
                meta = json.loads(z.read("config.json").decode())
                names = [n[6:] for n in z.namelist()
                         if n.startswith("media/") and n.endswith(".vault")]
            if not isinstance(meta, dict) or "check" not in meta:
                raise ValueError("no check")
        except Exception:
            raise Exception("This is not a Vault backup")
        return meta, names

    def import_backup(self, zpath, master, newpin, cb=None):
        meta, names = self.read_backup(zpath)
        if not self.master_ok(master, meta):
            raise Exception("Wrong Recovery Key")
        if self.media_list() or self.plain_files():
            raise Exception("Import works only on an empty vault")
        copied = []
        try:
            with zipfile.ZipFile(zpath) as z:
                for i, n in enumerate(names):
                    if os.path.basename(n) != n or n.startswith("."):
                        continue
                    dst = os.path.join(self.media, n)
                    tmp = dst + ".part"
                    try:
                        with z.open("media/" + n) as fi, open(tmp, "wb") as fo:
                            shutil.copyfileobj(fi, fo, 1 << 20)
                        os.replace(tmp, dst)
                    except Exception:
                        _rm(tmp)
                        raise
                    copied.append(dst)
                    if cb:
                        cb(i + 1, len(names))
        except Exception:
            for d in copied:
                _rm(d)
            raise
        have = set(os.listdir(self.media))
        albums = meta.get("albums", {})
        self.cfg["albums"] = {a: [x for x in lst if x in have]
                              for a, lst in albums.items() if isinstance(lst, list)}
        self.cfg["album_kind"] = meta.get("album_kind", {})
        self.cfg["autolock"] = meta.get("autolock", 60)
        self.cfg["fails"], self.cfg["lock_until"] = 0, 0
        self.setup_keys(newpin, master)
        return len(copied)

    # ----- encrypt / decrypt -----
    def encrypt_file(self, src, dst, key=None):
        key = key or self.key
        if key is None:
            raise Exception("Vault locked")
        nonce = os.urandom(16)
        tmp = dst + ".part"
        try:
            with open(src, "rb") as fi, open(tmp, "wb") as fo:
                fo.write(MAGIC + nonce)
                idx = 0
                while True:
                    b = fi.read(CHUNK)
                    if not b:
                        break
                    fo.write(_xor(b, _ks(key, nonce, idx, len(b))))
                    idx += 1
            os.replace(tmp, dst)
        except Exception:
            _rm(tmp)
            raise

    def decrypt_file(self, src, dst, key=None):
        key = key or self.key
        if key is None:
            raise Exception("Vault locked")
        try:
            with open(src, "rb") as fi, open(dst, "wb") as fo:
                head = fi.read(20)
                if head[:4] != MAGIC:
                    raise Exception("Bad file format")
                nonce = head[4:]
                idx = 0
                while True:
                    b = fi.read(CHUNK)
                    if not b:
                        break
                    fo.write(_xor(b, _ks(key, nonce, idx, len(b))))
                    idx += 1
        except Exception:
            _rm(dst)
            raise

    def find(self, name):
        p = os.path.join(self.media, name)
        return p if os.path.exists(p) else os.path.join(self.trash, name)

    def cache_path(self, name):
        return os.path.join(self.cache, base_name(name))

    def thumb_path(self, name):
        return os.path.join(self.cache, "t_" + base_name(name) + ".jpg")

    def decrypt_cached(self, name, key):
        if key is None:
            raise Exception("Vault locked")
        path = self.cache_path(name)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        tmp = path + f".{threading.get_ident()}.tmp"
        self.decrypt_file(self.find(name), tmp, key)
        os.replace(tmp, path)
        return path

    def thumb_cached(self, name, key):
        if key is None:
            raise Exception("Vault locked")
        tp = self.thumb_path(name)
        if os.path.exists(tp) and os.path.getsize(tp) > 0:
            return tp
        full = self.cache_path(name)
        tmp = None
        if os.path.exists(full) and os.path.getsize(full) > 0:
            src = full
        else:
            tmp = full + f".{threading.get_ident()}.tmp"
            self.decrypt_file(self.find(name), tmp, key)
            src = tmp
        if is_video(name):
            vt = make_video_thumb(src, tp)
            if tmp:
                os.replace(tmp, full)
                tmp = None
            return vt or None
        result = None
        if PILImage is not None:
            part = tp + f".{threading.get_ident()}.part"
            try:
                im = PILImage.open(src)
                try:
                    im.draft("RGB", (512, 512))
                except Exception:
                    pass
                im = im.convert("RGB")
                im.thumbnail((360, 360))
                im.save(part, "JPEG", quality=80)
                os.replace(part, tp)
                result = tp
            except Exception:
                result = None
            finally:
                _rm(part)
        if result is None:
            if tmp:
                os.replace(tmp, full)
                tmp = None
            result = full
        if tmp:
            _rm(tmp)
        return result

    def restore_file(self, name, key):
        fname = base_name(name)
        tmp = os.path.join(self.cache, f"restore_{threading.get_ident()}_{fname}")
        self.decrypt_file(self.find(name), tmp, key)
        try:
            return export_to_gallery(tmp, fname)
        finally:
            _rm(tmp)

    def clear_cache(self):
        try:
            for f in os.listdir(self.cache):
                _rm(os.path.join(self.cache, f))
        except Exception:
            pass

    # ----- media -----
    def media_list(self, kind="all"):
        files = [f for f in os.listdir(self.media)
                 if f.endswith(".vault") and base_name(f).lower().endswith(ALL_EXTS)]
        if kind == "photo":
            files = [f for f in files if not is_video(f)]
        elif kind == "video":
            files = [f for f in files if is_video(f)]
        return sorted(files, reverse=True)

    def album_items(self, album, kind="all"):
        inside = set(self.cfg["albums"].get(album, []))
        return [n for n in self.media_list(kind) if n in inside]

    def plain_files(self):
        return [f for f in os.listdir(self.media) if f.lower().endswith(ALL_EXTS)]

    def migrate_plain(self, cb=None):
        files = self.plain_files()
        done = 0
        for i, f in enumerate(files):
            src = os.path.join(self.media, f)
            try:
                self.encrypt_file(src, src + ".vault")
                os.remove(src)
                for lst in self.cfg["albums"].values():
                    if f in lst:
                        lst[lst.index(f)] = f + ".vault"
                done += 1
            except Exception:
                pass
            if cb:
                cb(i + 1, len(files))
        self.save()
        return done

    def add_one(self, path, i, move=False, key=None):
        """Returns (vault_name, None) on success or (None, error_text)."""
        try:
            ext = os.path.splitext(path)[1].lower()
            name = f"{int(time.time() * 1000)}_{i}{ext}.vault"
            self.encrypt_file(path, os.path.join(self.media, name), key)
            if move:
                _rm(path)
            return name, None
        except Exception as e:
            return None, str(e)

    # ----- delete / recycle bin -----
    def _drop_cache(self, name):
        _rm(self.cache_path(name))
        _rm(self.thumb_path(name))

    def _unlink_albums(self, name):
        for a in self.cfg["albums"].values():
            if name in a:
                a.remove(name)

    def delete(self, name):
        """Permanent delete (from vault or recycle bin)."""
        _rm(os.path.join(self.media, name))
        _rm(os.path.join(self.trash, name))
        self._drop_cache(name)
        self._unlink_albums(name)
        self.cfg["trash"].pop(name, None)
        self.save()

    def trash_move(self, name):
        src, dst = os.path.join(self.media, name), os.path.join(self.trash, name)
        al = [a for a, lst in self.cfg["albums"].items() if name in lst]
        shutil.move(src, dst)
        self._drop_cache(name)
        self._unlink_albums(name)
        self.cfg["trash"][name] = {"t": time.time(), "albums": al}
        self.save()

    def trash_list(self):
        tr = self.cfg["trash"]
        names = [n for n in tr if os.path.exists(os.path.join(self.trash, n))]
        return sorted(names, key=lambda n: tr[n].get("t", 0), reverse=True)

    def trash_restore(self, name):
        shutil.move(os.path.join(self.trash, name), os.path.join(self.media, name))
        rec = self.cfg["trash"].pop(name, {})
        for a in rec.get("albums", []):
            if a in self.cfg["albums"]:
                self.cfg["albums"][a].append(name)
        self.save()

    def purge_trash(self):
        limit = time.time() - TRASH_DAYS * 86400
        for n, rec in list(self.cfg["trash"].items()):
            if rec.get("t", 0) < limit:
                self.delete(n)

    # ----- folders -----
    def folders_for(self, kind):
        base = ["Pictures", "Downloads"] if kind == "photo" else ["Downloads", "Videos"]
        ak = self.cfg["album_kind"]
        extra = [a for a in self.cfg["albums"]
                 if a not in DEFAULT_FOLDERS and ak.get(a, kind) == kind]
        return base + extra

    def create_album(self, name, kind="photo"):
        if name and name not in self.cfg["albums"] and name not in DEFAULT_FOLDERS:
            self.cfg["albums"][name] = []
            self.cfg["album_kind"][name] = kind
            self.save()
            return True
        return False

    def delete_album(self, name):
        if name in DEFAULT_FOLDERS:
            return
        self.cfg["albums"].pop(name, None)
        self.cfg["album_kind"].pop(name, None)
        self.save()

    def add_to_album(self, album, name):
        lst = self.cfg["albums"].setdefault(album, [])
        if name not in lst:
            lst.append(name)
        self.save()

    def add_many_to_album(self, album, names):
        lst = self.cfg["albums"].setdefault(album, [])
        for n in names:
            if n not in lst:
                lst.append(n)
        self.save()

    def move_to_album(self, name, src, dst):
        if src and name in self.cfg["albums"].get(src, []):
            self.cfg["albums"][src].remove(name)
        self.add_to_album(dst, name)


# ---------------- UI widgets ----------------
class Label(KLabel):
    def __init__(self, **kw):
        kw.setdefault("color", hx(TXT))
        super().__init__(**kw)


class RBtn(Button):
    """Rounded flat button. Text colour adapts to the background."""

    def __init__(self, bg=None, radius=12, **kw):
        kw.setdefault("bold", True)
        super().__init__(**kw)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self._rgba = hx(bg or CARD)
        with self.canvas.before:
            self._col = Color(*self._rgba)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(radius)])
        self.bind(pos=self._sync, size=self._sync, state=self._paint)
        self._paint()

    def _sync(self, *a):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def _paint(self, *a):
        r, g, b, al = self._rgba
        k = .72 if self.state == "down" else 1
        self._col.rgba = (r * k, g * k, b * k, al)
        self.color = (1, 1, 1, 1) if (.299 * r + .587 * g + .114 * b) < .62 else hx("#101114")

    def set_bg(self, color):
        self._rgba = hx(color)
        self._paint()


class Card(BoxLayout):
    def __init__(self, bg=None, radius=10, **kw):
        super().__init__(**kw)
        with self.canvas.before:
            Color(*hx(bg or CARD))
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(radius)])
        self.bind(pos=self._s, size=self._s)

    def _s(self, *a):
        self._r.pos = self.pos
        self._r.size = self.size


class FolderIcon(Widget):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self.draw, size=self.draw)

    def draw(self, *a):
        self.canvas.clear()
        s = min(self.width, self.height) * .85
        if s <= 0:
            return
        x, y = self.center_x - s / 2, self.center_y - s * .4
        with self.canvas:
            Color(.96, .62, .05, 1)
            RoundedRectangle(pos=(x + s * .02, y + s * .2), size=(s * .9, s * .7), radius=[dp(8)])
            Color(1, 1, 1, .9)
            RoundedRectangle(pos=(x + s * .1, y + s * .3), size=(s * .76, s * .55), radius=[dp(4)])
            Color(.99, .78, .3, 1)
            RoundedRectangle(pos=(x, y), size=(s, s * .68), radius=[dp(10)])


class Cover(AsyncImage):
    def __init__(self, **kw):
        super().__init__(**kw)
        if hasattr(Image, "fit_mode"):
            self.fit_mode = "cover"
        else:
            self.allow_stretch = True
            self.keep_ratio = False
        with self.canvas.before:
            Color(*hx(CARD))
            self._ph = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])
        self.color = (1, 1, 1, 0)
        self.bind(pos=self._sync, size=self._sync, texture=self._tex)

    def _tex(self, *a):
        self.color = (1, 1, 1, 1 if self.texture else 0)

    def _sync(self, *a):
        self._ph.pos = self.pos
        self._ph.size = self.size


class Thumb(ButtonBehavior, Cover):
    """Picture tile. Shows a tick when selected and a play badge for videos."""
    sel = False
    vid = False

    def set_sel(self, on):
        self.sel = on
        self._redraw()

    def set_vid(self, on):
        self.vid = on
        self._redraw()

    def _redraw(self):
        self.canvas.after.clear()
        with self.canvas.after:
            if self.sel:
                Color(0, 0, 0, .45)
                RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])
                Color(*hx(ACCENT))
                Ellipse(pos=(self.right - dp(30), self.top - dp(30)),
                        size=(dp(22), dp(22)))
            if self.vid:
                s = min(self.width, self.height) * .3
                cx, cy = self.center_x, self.center_y
                Color(0, 0, 0, .5)
                Ellipse(pos=(cx - s * .75, cy - s * .75), size=(s * 1.5, s * 1.5))
                Color(1, 1, 1, .95)
                Triangle(points=[cx - s * .25, cy - s * .4,
                                 cx - s * .25, cy + s * .4,
                                 cx + s * .45, cy])

    def _sync(self, *a):
        super()._sync(*a)
        if self.sel or self.vid:
            self._redraw()


def lab(text, size=14, bold=False, color=None, halign="left", h=None):
    l = Label(text=text, font_size=dp(size), bold=bold, halign=halign, valign="middle",
              shorten=True, color=hx(color or TXT))
    l.bind(size=lambda w, s: setattr(w, "text_size", s))
    if h:
        l.size_hint_y, l.height = None, h
    return l


class FolderCell(ButtonBehavior, BoxLayout):
    """One folder, drawn according to the chosen Folder Style."""

    def __init__(self, style, title, count, has_cover, **kw):
        super().__init__(**kw)
        self.cover = None
        txt = f"{count} items"
        if style in ("default", "standard"):
            self.orientation = "vertical"
            self.add_widget(FolderIcon())
            self.add_widget(lab(title, 15, True, halign="center", h=dp(22)))
            self.add_widget(lab(txt, 12, color=MUTED, halign="center", h=dp(18)))
        elif style in ("large", "grid", "xlarge"):
            self.orientation = "vertical"
            if has_cover:
                self.cover = Cover()
                self.add_widget(self.cover)
            else:
                self.add_widget(FolderIcon())
            strip = Card(orientation="vertical", size_hint_y=None, height=dp(46),
                         padding=(dp(8), dp(2)))
            strip.add_widget(lab(title, 15, True))
            strip.add_widget(lab(txt, 12, color=MUTED))
            self.add_widget(strip)
        else:  # mini / list
            self.orientation = "horizontal"
            with self.canvas.before:
                Color(*hx(CARD))
                self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])
            self.bind(pos=self._bgs, size=self._bgs)
            w = dp(64) if style == "mini" else dp(92)
            if has_cover:
                self.cover = Cover(size_hint_x=None, width=w)
                self.add_widget(self.cover)
            else:
                ic = FolderIcon(size_hint_x=None, width=w)
                self.add_widget(ic)
            right = BoxLayout(orientation="vertical", padding=(dp(10), dp(4)))
            right.add_widget(lab(title, 15, True))
            right.add_widget(lab(txt, 12, color=MUTED))
            self.add_widget(right)

    def _bgs(self, *a):
        self._bg.pos = self.pos
        self._bg.size = self.size


class PinDots(Widget):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.n = 0
        self.bind(pos=self.draw, size=self.draw)

    def set(self, n):
        self.n = n
        self.draw()

    def draw(self, *a):
        self.canvas.clear()
        d, gap = dp(14), dp(10)
        with self.canvas:
            Color(*hx(ACCENT))
            if self.n:
                x0 = self.center_x - (self.n * d + (self.n - 1) * gap) / 2
                for i in range(self.n):
                    Ellipse(pos=(x0 + i * (d + gap), self.center_y - d / 2), size=(d, d))
            Line(points=[self.center_x - dp(130), self.y + dp(4),
                         self.center_x + dp(130), self.y + dp(4)], width=dp(1.2))


class SwipeStage(BoxLayout):
    def __init__(self, swipe_cb, **kw):
        super().__init__(**kw)
        self.swipe_cb = swipe_cb
        self._p = None

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self._p = (touch.x, touch.y, touch.uid)
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        p = self._p
        if p and p[2] == touch.uid:
            self._p = None
            dx, dy = touch.x - p[0], touch.y - p[1]
            if abs(dx) > dp(70) and abs(dx) > abs(dy) * 1.5:
                self.swipe_cb(-1 if dx > 0 else 1)
        return super().on_touch_up(touch)


# ---------------- UI helpers ----------------
def btn(text, cb, color=None, **kw):
    b = RBtn(text=text, bg=color or CARD, **kw)
    b.bind(on_release=lambda *a: cb())
    return b


def title_label(text, size=24):
    lbl = Label(text=text, font_size=dp(size), bold=True, halign="left", shorten=True)
    lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
    return lbl


def paint_bg(widget, color):
    with widget.canvas.before:
        Color(*hx(color))
        rect = Rectangle(pos=widget.pos, size=widget.size)
    widget.bind(pos=lambda w, v: setattr(rect, "pos", v),
                size=lambda w, v: setattr(rect, "size", v))


def header(title_widget, buttons, h=56):
    bar = BoxLayout(size_hint_y=None, height=dp(h), spacing=dp(6), padding=(dp(10), dp(6)))
    paint_bg(bar, HEAD)
    bar.add_widget(title_widget)
    for b in buttons:
        bar.add_widget(b)
    return bar


def popup(title, content, size_hint, auto_dismiss=True):
    return Popup(title=title, content=content, size_hint=size_hint,
                 auto_dismiss=auto_dismiss, background="",
                 background_color=hx(CARD), separator_color=hx(ACCENT),
                 title_color=hx(TXT))


def info(title, text, h=.3):
    lbl = Label(text=text, halign="center")
    lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
    pop = popup(title, lbl, (.88, h))
    pop.open()
    return pop


class SealCheck(Widget):
    """Green seal with a white tick."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self.draw, size=self.draw)

    def draw(self, *a):
        self.canvas.clear()
        s = min(self.width, self.height)
        if s <= 0:
            return
        cx, cy, R = self.center_x, self.center_y, s / 2
        n = 72
        verts = [cx, cy, 0, 0]
        for i in range(n + 1):
            ang = 2 * math.pi * i / n
            r = R * (0.9 + 0.1 * math.cos(12 * ang))
            verts += [cx + r * math.cos(ang), cy + r * math.sin(ang), 0, 0]
        with self.canvas:
            Color(.13, .63, .17, 1)
            Mesh(vertices=verts, indices=list(range(n + 2)), mode="triangle_fan")
            Color(1, 1, 1, 1)
            Line(points=[cx - R * .38, cy - R * .02, cx - R * .1, cy - R * .3,
                         cx + R * .4, cy + R * .28],
                 width=dp(3), cap="round", joint="round")


def success_popup(text, extra="", on_done=None):
    """Big 'imported successfully' box: green tick on top, text, Done button."""
    box = BoxLayout(orientation="vertical", spacing=dp(14), padding=dp(18))
    box.add_widget(SealCheck(size_hint_y=None, height=dp(84)))
    lbl = Label(text=text, font_size=dp(18), bold=True, halign="center",
                size_hint_y=None, height=dp(34))
    lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
    box.add_widget(lbl)
    h = dp(330)
    if extra:
        ex = Label(text=extra, font_size=dp(13), halign="center", color=hx(MUTED),
                   size_hint_y=None, height=dp(58))
        ex.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        box.add_widget(ex)
        h = dp(390)
    pop = Popup(title="", separator_height=0, content=box, size_hint=(.88, None),
                height=h, auto_dismiss=False, background="", background_color=hx(CARD))

    def close():
        pop.dismiss()
        if on_done:
            on_done()
    box.add_widget(btn("Done", close, ACCENT, size_hint_y=None, height=dp(54), radius=27))
    pop.open()
    return pop


def ask(title, text, yes_cb, yes_text="Yes"):
    box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
    lbl = Label(text=text, halign="center")
    lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
    box.add_widget(lbl)
    row = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(48))
    pop = popup(title, box, (.85, .4))
    row.add_widget(btn("Cancel", pop.dismiss, SOFT))
    row.add_widget(btn(yes_text, lambda: (pop.dismiss(), yes_cb()), RED))
    box.add_widget(row)
    pop.open()


def delete_dialog(count, on_confirm):
    """Delete popup with 'Move to Recycle Bin' option. on_confirm(permanent: bool)."""
    box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
    lbl = Label(text=f"Are you sure want to delete {count} file(s)?", halign="center")
    lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
    box.add_widget(lbl)
    state = {"bin": True}
    chk = RBtn(text="[X]  Move to Recycle Bin", bg=SOFT, size_hint_y=None, height=dp(44))
    box.add_widget(chk)
    pop = popup("Delete", box, (.88, .46))
    ok = btn("Delete", lambda: (pop.dismiss(), on_confirm(not state["bin"])), RED)

    def tog(*a):
        state["bin"] = not state["bin"]
        chk.text = ("[X]  " if state["bin"] else "[  ]  ") + "Move to Recycle Bin"
        ok.text = "Delete" if state["bin"] else "Delete Permanently"
    chk.bind(on_release=tog)
    row = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(48))
    row.add_widget(btn("Cancel", pop.dismiss, SOFT))
    row.add_widget(ok)
    box.add_widget(row)
    pop.open()


def new_album_popup(after, kind="photo"):
    app = App.get_running_app()
    box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
    ti = TextInput(hint_text="Folder name", multiline=False, size_hint_y=None, height=dp(46))
    box.add_widget(ti)
    pop = popup("New Folder", box, (.85, .32))

    def ok():
        n = ti.text.strip()
        if n and app.store.create_album(n, kind):
            pop.dismiss()
            after(n)
        else:
            ti.text = ""
            ti.hint_text = "Name is empty or already exists"
    box.add_widget(btn("Create", ok, ACCENT, size_hint_y=None, height=dp(46)))
    pop.open()


def folder_picker(title, folders, on_pick):
    box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
    pop = popup(title, box, (.85, .6))
    for a in folders:
        box.add_widget(btn(a, lambda a=a: (pop.dismiss(), on_pick(a))))
    box.add_widget(btn("Cancel", pop.dismiss, SOFT))
    pop.open()


def recovery_popup(after):
    app = App.get_running_app()
    st = app.store
    box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
    k = TextInput(hint_text="Paste Recovery Key", multiline=False,
                  size_hint_y=None, height=dp(46))
    p = TextInput(hint_text="New PIN (4-12 digits)", multiline=False, input_filter="int",
                  password=True, size_hint_y=None, height=dp(46))
    msg = Label(color=hx("#FF6B6B"), size_hint_y=None, height=dp(24), font_size=dp(13))
    for w in (k, p, msg):
        box.add_widget(w)
    pop = popup("Unlock with Recovery Key", box, (.92, .5))

    def ok():
        pin = p.text.strip()
        if not valid_pin(pin):
            msg.text = "PIN must be 4 to 12 digits"
            return
        m = st.parse_recovery_key(k.text)
        if m is None or not st.recover_with_key(m, pin):
            msg.text = "Wrong Recovery Key"
            return
        pop.dismiss()
        after()
    row = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(48))
    row.add_widget(btn("Cancel", pop.dismiss, SOFT))
    row.add_widget(btn("Unlock", ok, GREEN))
    box.add_widget(row)
    pop.open()


def make_tile(app, name, gen):
    """Thumbnail tile. Videos get a frame from the video plus a play badge."""
    t = Thumb()
    if is_video(name):
        t.set_vid(True)
    app.thumb_q.put((gen, t, name, app.store.key))
    return t


def mark(tile, on):
    if isinstance(tile, Thumb):
        tile.set_sel(on)
    else:
        tile.set_bg(ACCENT if on else SOFT)


# ---------------- PIN keypad (4 to 12 digits) ----------------
class PinPad(BoxLayout):
    def __init__(self, on_pin, **kw):
        super().__init__(orientation="vertical", padding=dp(24), spacing=dp(12), **kw)
        self.on_pin = on_pin
        self.buf = ""
        self.title = Label(font_size=dp(22), bold=True, size_hint_y=.13)
        self.dots = PinDots(size_hint_y=.1)
        self.msg = Label(color=hx("#FF6B6B"), size_hint_y=.08)
        grid = GridLayout(cols=3, spacing=dp(12))
        for k in ["1", "2", "3", "4", "5", "6", "7", "8", "9", "<", "0", "OK"]:
            col = ACCENT if k == "OK" else CARD
            b = RBtn(text=k, font_size=dp(26), bg=col, radius=16)
            b.bind(on_release=lambda x, k=k: self.press(k))
            grid.add_widget(b)
        for w in (self.title, self.dots, self.msg, grid):
            self.add_widget(w)

    def reset(self, title=None, msg=""):
        self.buf = ""
        if title:
            self.title.text = title
        self.msg.text = msg
        self.dots.set(0)

    def press(self, k):
        self.msg.text = ""
        if k == "<":
            self.buf = self.buf[:-1]
        elif k == "OK":
            if len(self.buf) < MIN_PIN:
                self.msg.text = "PIN must be 4 to 12 digits"
            else:
                pin = self.buf
                self.buf = ""
                self.dots.set(0)
                self.on_pin(pin)
                return
        elif len(self.buf) < MAX_PIN:
            self.buf += k
        self.dots.set(len(self.buf))


class PinScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.mode, self.first = "unlock", ""
        self.pad = PinPad(self.on_pin)
        root = BoxLayout(orientation="vertical", padding=(0, 0, 0, dp(8)))
        root.add_widget(self.pad)
        bx = BoxLayout(size_hint_y=None, height=dp(48), padding=(dp(20), 0))
        bx.add_widget(btn("Forgot PIN? Use Recovery Key", self.forgot, SOFT, font_size=dp(13)))
        root.add_widget(bx)
        self.add_widget(root)

    def forgot(self):
        app = App.get_running_app()
        if not app.store.has_keys():
            info("Recovery Key", "No vault key yet.\nCreate a PIN first.", .3)
            return
        recovery_popup(app.after_unlock)

    def on_pre_enter(self, *a):
        st = App.get_running_app().store
        self.first = ""
        if st.has_keys():
            self.mode, title = "unlock", "Enter PIN"
        elif st.is_legacy():
            self.mode, title = "legacy", "Enter PIN"
        else:
            self.mode, title = "create", "Create PIN (4-12 digits)"
        self.pad.reset(title)

    def on_pin(self, pin):
        app = App.get_running_app()
        st = app.store
        if self.mode in ("unlock", "legacy"):
            lu = st.cfg.get("lock_until", 0)
            if lu > time.time():
                self.pad.msg.text = f"Too many attempts. Try again in {int(lu - time.time()) + 1} sec"
                return
            if self.mode == "unlock":
                m = st.unlock(pin)
                ok = m is not None
                if ok:
                    st.key = m
            else:
                ok = st.check_legacy(pin)
                if ok:
                    st.setup_keys(pin)
            if ok:
                st.cfg["fails"], st.cfg["lock_until"] = 0, 0
                st.save()
                app.after_unlock()
            else:
                f = st.cfg.get("fails", 0) + 1
                st.cfg["fails"] = f
                msg = "Wrong PIN!"
                if f % 5 == 0:
                    wait = 30 * (f // 5)
                    st.cfg["lock_until"] = time.time() + wait
                    msg = f"{f} wrong attempts. Wait {wait} sec"
                st.save()
                self.pad.msg.text = msg
        elif self.mode == "create":
            self.first, self.mode = pin, "confirm"
            self.pad.reset("Confirm PIN")
        else:
            if pin == self.first:
                st.setup_keys(pin)
                app.after_unlock()
            else:
                self.mode = "create"
                self.pad.reset("Create PIN (4-12 digits)", "PINs do not match, try again")


class ChangePinScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        self.step, self.master, self.first = "old", None, ""
        root = BoxLayout(orientation="vertical", padding=(0, 0, 0, dp(8)))
        self.pad = PinPad(self.on_pin)
        root.add_widget(self.pad)
        bx = BoxLayout(size_hint_y=None, height=dp(48), padding=(dp(20), 0))
        bx.add_widget(btn("Cancel", lambda: app.go("settings"), SOFT))
        root.add_widget(bx)
        self.add_widget(root)

    def on_pre_enter(self, *a):
        self.step, self.master, self.first = "old", None, ""
        self.pad.reset("Enter current PIN")

    def on_pin(self, pin):
        app = App.get_running_app()
        st = app.store
        if self.step == "old":
            m = st.unlock(pin)
            if m is None:
                self.pad.reset(msg="Wrong PIN!")
            else:
                self.master, self.step = m, "new"
                self.pad.reset("Enter new PIN (4-12 digits)")
        elif self.step == "new":
            self.first, self.step = pin, "confirm"
            self.pad.reset("Confirm new PIN")
        else:
            if pin == self.first:
                st.rewrap(self.master, pin)
                info("Done", "PIN changed!")
                app.go("settings")
            else:
                self.step = "new"
                self.pad.reset("Enter new PIN (4-12 digits)", "PINs do not match")


# ---------------- Settings ----------------
class SettingsScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        root = BoxLayout(orientation="vertical")
        root.add_widget(header(title_label("Settings"), [
            btn("Back", lambda: app.go("home"), SOFT, size_hint_x=None, width=dp(70))], 64))
        sv = ScrollView()
        body = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(12), size_hint_y=None)
        body.bind(minimum_height=body.setter("height"))

        def row(text, cb, color=None):
            body.add_widget(btn(text, cb, color, size_hint_y=None, height=dp(52)))

        def section(text):
            body.add_widget(lab(text, 14, True, MUTED, h=dp(30)))

        row("Change Password", lambda: app.go("changepin"), ACCENT)
        row("Show Recovery Key", self.show_key, GREEN)
        row("Create Backup (Drive/Downloads)", self.backup)
        row("Import from Backup", self.import_backup)
        section("Advance")
        row("App Themes", lambda: app.go("theme"))
        row("Folder Style", lambda: app.go("folderstyle"))
        row("Recycle Bin", lambda: app.go("trash"))
        row("Change Language", lambda: app.go("language"))
        section("Auto-lock (when idle)")
        arow = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.abtns = {}
        for label, sec in AUTOLOCK_OPTS:
            b = btn(label, lambda s=sec: self.set_auto(s), font_size=dp(13), radius=18)
            self.abtns[sec] = b
            arow.add_widget(b)
        body.add_widget(arow)
        note = Label(
            text=("Note:\n"
                  "- Save the Recovery Key (e.g. in your Gmail). Without it, files cannot be "
                  "recovered if you forget the PIN or uninstall the app.\n"
                  "- Keep the backup file in Drive. It opens only with the Recovery Key.\n"
                  "- Unhidden files go to the Gallery folder VaultRestored."),
            halign="left", valign="top", color=hx(MUTED), font_size=dp(13),
            size_hint_y=None, height=dp(190))
        note.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        body.add_widget(note)
        sv.add_widget(body)
        root.add_widget(sv)
        self.add_widget(root)

    def on_pre_enter(self, *a):
        cur = App.get_running_app().store.cfg.get("autolock", 60)
        for sec, b in self.abtns.items():
            b.set_bg(ACCENT if sec == cur else CARD)

    def set_auto(self, sec):
        st = App.get_running_app().store
        st.cfg["autolock"] = sec
        st.save()
        self.on_pre_enter()

    def show_key(self):
        app = App.get_running_app()
        try:
            key = app.store.recovery_key()
        except Exception as e:
            info("Recovery Key", str(e), .3)
            return
        parts = key.split("-")
        text = "\n".join("  ".join(parts[i:i + 3]) for i in range(0, len(parts), 3))
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        lbl = Label(text=text, font_size=dp(17), halign="center")
        lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        box.add_widget(lbl)
        note = Label(text="Send it to yourself on Gmail or save it in Notes.\n"
                          "Do not share it: it can open the whole vault.",
                     halign="center", color=hx(MUTED), font_size=dp(13),
                     size_hint_y=None, height=dp(60))
        note.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        box.add_widget(note)
        pop = popup("Recovery Key", box, (.94, .7))
        row = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(48))
        holder = {}

        def do_copy():
            try:
                from kivy.core.clipboard import Clipboard
                Clipboard.copy(key)
                holder["b"].text = "Copied!"
            except Exception:
                holder["b"].text = "Copy failed"

        holder["b"] = btn("Copy", do_copy, ACCENT)
        row.add_widget(holder["b"])
        row.add_widget(btn("Close", pop.dismiss, SOFT))
        box.add_widget(row)
        pop.open()

    def backup(self):
        ask("Create Backup",
            "The backup cannot be opened without the Recovery Key.\n"
            "Save the key first with 'Show Recovery Key'.\n\nContinue?",
            self._pick_backup_dest, "Continue")

    def _pick_backup_dest(self):
        app = App.get_running_app()
        fname = "VaultBackup_" + time.strftime("%Y%m%d") + ".zip"
        app.busy = True

        def got(uri):
            Clock.schedule_once(lambda dt: self._do_backup(uri))

        if not android_document("create", BACKUP_CODE, got, fname):
            app.busy = False
            info("Backup", "This works only in the Android APK.", .3)

    def _do_backup(self, uri):
        app = App.get_running_app()
        app.last = time.time()
        if not uri:
            app.busy = False
            return
        lbl = Label(text="Creating backup...", halign="center")
        pop = popup("Backup", lbl, (.8, .25), auto_dismiss=False)
        pop.open()
        tmp = os.path.join(app.user_data_dir, "backup_tmp.zip")

        def prog(i, n):
            Clock.schedule_once(lambda dt: setattr(lbl, "text", f"Backup {i}/{n}"))

        def done(n, err):
            pop.dismiss()
            app.busy = False
            app.last = time.time()
            if err:
                info("Backup failed", err[:150], .4)
            elif n == 0:
                info("Backup", "Vault is empty, nothing was backed up.", .3)
            else:
                info("Backup done", f"{n} files saved.\nKeep it in Google Drive and "
                     "keep the Recovery Key somewhere else.", .4)

        def work():
            err, n = None, 0
            try:
                n = app.store.make_backup(tmp, prog)
                Clock.schedule_once(lambda dt: setattr(lbl, "text", "Saving..."))
                copy_path_to_uri(tmp, uri)
            except Exception as e:
                err = str(e)
            _rm(tmp)
            Clock.schedule_once(lambda dt: done(n, err))

        threading.Thread(target=work, daemon=True).start()

    def import_backup(self):
        app = App.get_running_app()
        if app.store.media_list():
            info("Import", "Import works only on an empty vault.\n"
                 "Reinstall the app, create a PIN, then import here.", .4)
            return
        app.busy = True

        def got(uri):
            Clock.schedule_once(lambda dt: self._got_backup(uri))

        if not android_document("open", OPEN_CODE, got):
            app.busy = False
            info("Import", "This works only in the Android APK.", .3)

    def _got_backup(self, uri):
        app = App.get_running_app()
        app.last = time.time()
        if not uri:
            app.busy = False
            return
        lbl = Label(text="Reading backup...", halign="center")
        pop = popup("Import", lbl, (.8, .25), auto_dismiss=False)
        pop.open()
        tmp = os.path.join(app.user_data_dir, "import_tmp.zip")

        def fail(err):
            pop.dismiss()
            app.busy = False
            _rm(tmp)
            info("Import failed", err[:150], .4)

        def ready(n):
            pop.dismiss()
            self._ask_key(tmp, n)

        def work():
            try:
                copy_uri_to_path(uri, tmp)
                meta, names = app.store.read_backup(tmp)
            except Exception as e:
                msg = str(e)
                Clock.schedule_once(lambda dt: fail(msg))
                return
            Clock.schedule_once(lambda dt: ready(len(names)))

        threading.Thread(target=work, daemon=True).start()

    def _ask_key(self, tmp, count):
        app = App.get_running_app()
        st = app.store
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        box.add_widget(Label(text=f"Backup has {count} files.", size_hint_y=None,
                             height=dp(24), font_size=dp(14)))
        k = TextInput(hint_text="Paste Recovery Key", multiline=False,
                      size_hint_y=None, height=dp(46))
        p = TextInput(hint_text="New PIN (4-12 digits)", multiline=False, input_filter="int",
                      password=True, size_hint_y=None, height=dp(46))
        msg = Label(color=hx("#FF6B6B"), size_hint_y=None, height=dp(24), font_size=dp(13))
        for w in (k, p, msg):
            box.add_widget(w)
        pop = popup("Import Backup", box, (.92, .55), auto_dismiss=False)

        def cancel():
            pop.dismiss()
            app.busy = False
            _rm(tmp)

        def go():
            pin = p.text.strip()
            if not valid_pin(pin):
                msg.text = "PIN must be 4 to 12 digits"
                return
            m = st.parse_recovery_key(k.text)
            if m is None:
                msg.text = "Wrong Recovery Key"
                return
            pop.dismiss()
            self._run_import(tmp, m, pin)

        row = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(48))
        row.add_widget(btn("Cancel", cancel, SOFT))
        row.add_widget(btn("Import", go, GREEN))
        box.add_widget(row)
        pop.open()

    def _run_import(self, tmp, master, pin):
        app = App.get_running_app()
        lbl = Label(text="Importing...", halign="center")
        pop = popup("Import", lbl, (.8, .25), auto_dismiss=False)
        pop.open()

        def prog(i, n):
            Clock.schedule_once(lambda dt: setattr(lbl, "text", f"Import {i}/{n}"))

        def done(n, err):
            pop.dismiss()
            app.busy = False
            app.last = time.time()
            _rm(tmp)
            if err:
                info("Import failed", err[:150], .4)
                return
            info("Import done", f"{n} files restored.\nYour new PIN is now active.", .4)
            app.kind = "photo"
            app.go("home")

        def work():
            n, err = 0, None
            try:
                n = app.store.import_backup(tmp, master, pin, prog)
            except Exception as e:
                err = str(e)
            Clock.schedule_once(lambda dt: done(n, err))

        threading.Thread(target=work, daemon=True).start()


# ---------------- Theme / Folder Style / Language ----------------
class ThemeScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        root = BoxLayout(orientation="vertical")
        root.add_widget(header(title_label("App Themes"), [
            btn("Back", lambda: app.go("settings"), SOFT, size_hint_x=None, width=dp(70))], 64))
        body = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        body.add_widget(lab("Mode", 15, True, MUTED, h=dp(28)))
        mrow = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        self.mbtns = {}
        for key, label in [("light", "Light"), ("auto", "Auto"), ("night", "Night")]:
            b = btn(label, lambda k=key: self.set_mode(k), radius=18)
            self.mbtns[key] = b
            mrow.add_widget(b)
        body.add_widget(mrow)
        body.add_widget(lab("Set Color", 15, True, MUTED, h=dp(34)))
        grid = GridLayout(cols=4, spacing=dp(10), size_hint_y=None, height=dp(140))
        self.cbtns = []
        for i, (pastel, acc) in enumerate(SCHEMES):
            b = RBtn(bg=pastel, radius=12)
            b.bind(on_release=lambda w, i=i: self.set_scheme(i))
            self.cbtns.append(b)
            grid.add_widget(b)
        body.add_widget(grid)
        body.add_widget(lab("Auto = Night theme from 7 PM to 6 AM", 12, color=MUTED, h=dp(24)))
        body.add_widget(Widget())
        root.add_widget(body)
        self.add_widget(root)

    def on_pre_enter(self, *a):
        cfg = App.get_running_app().store.cfg
        for k, b in self.mbtns.items():
            b.set_bg(ACCENT if k == cfg.get("theme", "light") else CARD)
        for i, b in enumerate(self.cbtns):
            b.text = "OK" if i == cfg.get("scheme", 0) else ""

    def set_mode(self, k):
        app = App.get_running_app()
        app.store.cfg["theme"] = k
        app.store.save()
        app.restyle("theme")

    def set_scheme(self, i):
        app = App.get_running_app()
        app.store.cfg["scheme"] = i
        app.store.save()
        app.restyle("theme")


class FolderStyleScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        root = BoxLayout(orientation="vertical")
        root.add_widget(header(title_label("Folder Style"), [
            btn("Back", lambda: app.go("home"), SOFT, size_hint_x=None, width=dp(70))], 64))
        sv = ScrollView()
        body = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(12), size_hint_y=None)
        body.bind(minimum_height=body.setter("height"))
        self.rows = {}
        for key, label, cols, h, desc in FOLDER_STYLES:
            b = RBtn(text=f"{label}\n{desc}", bg=CARD, radius=12, size_hint_y=None,
                     height=dp(66), halign="left")
            b.bind(size=lambda w, s: setattr(w, "text_size", (s[0] - dp(24), None)))
            b.bind(on_release=lambda w, k=key: self.pick(k))
            self.rows[key] = b
            body.add_widget(b)
        sv.add_widget(body)
        root.add_widget(sv)
        self.add_widget(root)

    def on_pre_enter(self, *a):
        cur = App.get_running_app().store.cfg.get("folder_style", "default")
        for k, b in self.rows.items():
            b.set_bg(ACCENT if k == cur else CARD)

    def pick(self, k):
        st = App.get_running_app().store
        st.cfg["folder_style"] = k
        st.save()
        self.on_pre_enter()


class LanguageScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        root = BoxLayout(orientation="vertical")
        root.add_widget(header(title_label("Select Language"), [
            btn("Back", lambda: app.go("settings"), SOFT, size_hint_x=None, width=dp(70))], 64))
        sv = ScrollView()
        body = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(12), size_hint_y=None)
        body.bind(minimum_height=body.setter("height"))
        self.rows = {}
        for code, name in LANGS:
            b = RBtn(text=name, bg=CARD, radius=12, size_hint_y=None, height=dp(52),
                     halign="left")
            b.bind(size=lambda w, s: setattr(w, "text_size", (s[0] - dp(24), None)))
            b.bind(on_release=lambda w, c=code, n=name: self.pick(c, n))
            self.rows[code] = b
            body.add_widget(b)
        sv.add_widget(body)
        root.add_widget(sv)
        self.add_widget(root)

    def on_pre_enter(self, *a):
        cur = App.get_running_app().store.cfg.get("lang", "en")
        for c, b in self.rows.items():
            b.set_bg(ACCENT if c == cur else CARD)

    def pick(self, code, name):
        if code not in AVAILABLE_LANGS:
            info("Language", f"{name} is not available yet.\nThe app is currently English only.", .32)
            return
        st = App.get_running_app().store
        st.cfg["lang"] = code
        st.save()
        self.on_pre_enter()


# ---------------- Recycle Bin ----------------
class TrashScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        self.sel, self.tiles = set(), {}
        root = BoxLayout(orientation="vertical")
        root.add_widget(header(title_label("Recycle Bin"), [
            btn("Back", lambda: app.go("settings"), SOFT, size_hint_x=None, width=dp(70))], 64))
        self.mid = BoxLayout()
        self.empty = Label(text="Recycle bin is empty.\nFiles in the recycle bin are deleted "
                                "automatically after 30 days.",
                           halign="center", color=hx(MUTED), font_size=dp(15))
        self.empty.bind(size=lambda w, s: setattr(w, "text_size", (s[0] - dp(40), None)))
        self.sv = ScrollView()
        self.grid = GridLayout(cols=3, spacing=dp(6), padding=dp(4),
                               size_hint_y=None, row_force_default=True)
        self.grid.bind(minimum_height=self.grid.setter("height"))
        self.sv.add_widget(self.grid)
        root.add_widget(self.mid)
        self.bar = BoxLayout(size_hint_y=None, height=dp(60), spacing=dp(8), padding=dp(8))
        self.bar.add_widget(btn("Restore", self.restore, GREEN))
        self.bar.add_widget(btn("Delete Forever", self.delete, RED))
        root.add_widget(self.bar)
        self.add_widget(root)

    def on_pre_enter(self, *a):
        self.sel.clear()
        self.refresh()

    def refresh(self):
        app = App.get_running_app()
        st = app.store
        self.mid.clear_widgets()
        self.grid.clear_widgets()
        self.tiles = {}
        if st.key is None:
            return
        names = st.trash_list()
        self.bar.opacity = 1 if names else 0
        self.bar.disabled = not names
        if not names:
            self.mid.add_widget(self.empty)
            return
        self.mid.add_widget(self.sv)
        app.gen += 1
        self.grid.row_default_height = (Window.width - dp(20)) / 3
        for n in names:
            t = make_tile(app, n, app.gen)
            t.bind(on_release=lambda w, n=n: self.toggle(n))
            self.tiles[n] = t
            mark(t, n in self.sel)
            self.grid.add_widget(t)

    def toggle(self, n):
        if n in self.sel:
            self.sel.discard(n)
        else:
            self.sel.add(n)
        mark(self.tiles[n], n in self.sel)

    def restore(self):
        if not self.sel:
            info("Select files", "Tap the files you want to restore.", .28)
            return
        st = App.get_running_app().store
        for n in list(self.sel):
            try:
                st.trash_restore(n)
            except Exception:
                pass
        self.sel.clear()
        self.refresh()

    def delete(self):
        if not self.sel:
            info("Select files", "Tap the files you want to delete.", .28)
            return

        def go():
            st = App.get_running_app().store
            for n in list(self.sel):
                st.delete(n)
            self.sel.clear()
            self.refresh()
        ask("Delete Forever", f"Delete {len(self.sel)} file(s) permanently?", go, "Delete")


# ---------------- Home (Photos / Videos tabs with folders) ----------------
class HomeScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        root = BoxLayout(orientation="vertical")
        self.title = title_label("Photos")

        def hb(text, cb, w):
            return btn(text, cb, CARD, size_hint_x=None, width=dp(w), font_size=dp(12))
        root.add_widget(header(self.title, [
            hb("Style", lambda: app.go("folderstyle"), 54),
            hb("Bin", lambda: app.go("trash"), 46),
            hb("Lock", app.lock, 52),
            hb("Settings", lambda: app.go("settings"), 72)], 64))

        top = BoxLayout(size_hint_y=None, height=dp(84), spacing=dp(10), padding=dp(10))
        self.create_btn = btn("Create\nNew folder  +", self.new_folder, CARD, radius=20)
        self.all_btn = btn("All Photos\n0 Files", self.open_all, ACCENT, radius=20)
        top.add_widget(self.create_btn)
        top.add_widget(self.all_btn)
        root.add_widget(top)

        sv = ScrollView()
        self.grid = GridLayout(cols=2, spacing=dp(10), padding=dp(10),
                               size_hint_y=None, row_force_default=True)
        self.grid.bind(minimum_height=self.grid.setter("height"))
        sv.add_widget(self.grid)
        root.add_widget(sv)

        nav = BoxLayout(size_hint_y=None, height=dp(62), padding=(dp(8), dp(8)), spacing=dp(6))
        paint_bg(nav, NAVBG)
        self.tabs = {}
        for label, kind in [("Photos", "photo"), ("Videos", "video")]:
            b = btn(label, lambda k=kind: self.switch(k), NAVBG, radius=14)
            self.tabs[kind] = b
            nav.add_widget(b)
        root.add_widget(nav)
        self.add_widget(root)

    def on_pre_enter(self, *a):
        self.refresh()

    def switch(self, kind):
        App.get_running_app().kind = kind
        self.refresh()

    def new_folder(self):
        app = App.get_running_app()
        new_album_popup(lambda n: self.refresh(), app.kind)

    def open_all(self):
        App.get_running_app().open_folder(None)

    def refresh(self):
        app = App.get_running_app()
        st, kind = app.store, app.kind
        self.grid.clear_widgets()
        if st.key is None:
            return
        word = "Photos" if kind == "photo" else "Videos"
        self.title.text = word
        for k, b in self.tabs.items():
            b.set_bg(ACCENT if k == kind else NAVBG)
        self.all_btn.text = f"All {word}\n{len(st.media_list(kind))} Files"
        style = st.cfg.get("folder_style", "default")
        cols, h = 2, 170
        for key, label, c, hh, desc in FOLDER_STYLES:
            if key == style:
                cols, h = c, hh
        self.grid.cols = cols
        self.grid.row_default_height = dp(h)
        app.gen += 1
        gen = app.gen
        for a in st.folders_for(kind):
            items = st.album_items(a, kind)
            photos = items
            cell = FolderCell(style, a, len(items), bool(photos))
            if photos and cell.cover is not None:
                app.thumb_q.put((gen, cell.cover, photos[0], st.key))
            cell.bind(on_release=lambda w, a=a: app.open_folder(a))
            self.grid.add_widget(cell)


# ---------------- Folder contents ----------------
class FolderScreen(Screen):
    PAGE = 90

    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        self.names, self.limit = [], self.PAGE
        self.sel_mode, self.sel, self.tiles = False, set(), {}
        root = BoxLayout(orientation="vertical")
        tbox = BoxLayout(orientation="vertical")
        self.title = Label(font_size=dp(22), bold=True, halign="left", valign="bottom",
                           shorten=True)
        self.title.bind(size=lambda w, s: setattr(w, "text_size", s))
        self.count = Label(font_size=dp(13), color=hx(MUTED), halign="left", valign="top",
                           shorten=True)
        self.count.bind(size=lambda w, s: setattr(w, "text_size", s))
        tbox.add_widget(self.title)
        tbox.add_widget(self.count)
        self.del_btn = btn("Del", self.del_folder, RED, size_hint_x=None, width=0,
                           opacity=0, disabled=True)
        self.sel_btn = btn("Select", self.toggle_select_mode, CARD, size_hint_x=None,
                           width=dp(72), font_size=dp(13))
        root.add_widget(header(tbox, [
            self.del_btn, self.sel_btn,
            btn("Back", self.back, SOFT, size_hint_x=None, width=dp(64), font_size=dp(13))], 64))

        self.imp_box = BoxLayout(size_hint_y=None, height=dp(66), padding=dp(8))
        self.imp = btn("Import  +", self.start_import, CARD, radius=26)
        self.imp_box.add_widget(self.imp)
        root.add_widget(self.imp_box)

        sv = ScrollView()
        self.grid = GridLayout(cols=3, spacing=dp(6), padding=dp(4),
                               size_hint_y=None, row_force_default=True)
        self.grid.bind(minimum_height=self.grid.setter("height"))
        sv.add_widget(self.grid)
        root.add_widget(sv)

        self.bar = BoxLayout(size_hint_y=None, height=0, opacity=0, disabled=True,
                             spacing=dp(8), padding=dp(8))
        self.bar.add_widget(btn("Move", self.do_move, ACCENT))
        self.bar.add_widget(btn("Unhide", self.do_unhide, GREEN))
        self.bar.add_widget(btn("Delete", self.do_delete, RED))
        root.add_widget(self.bar)
        self.add_widget(root)

    # ----- helpers -----
    def _show(self, widget, on, h):
        widget.height = h if on else 0
        widget.opacity = 1 if on else 0
        widget.disabled = not on

    def back(self):
        if self.sel_mode:
            self.set_select_mode(False)
        else:
            App.get_running_app().go("home")

    def on_pre_enter(self, *a):
        self.set_select_mode(False, refresh=False)
        self.refresh()

    def set_select_mode(self, on, refresh=True):
        self.sel_mode = on
        self.sel.clear()
        self.sel_btn.text = "Cancel" if on else "Select"
        self._show(self.bar, on, dp(64))
        if refresh:
            self.refresh(False)

    def toggle_select_mode(self):
        self.set_select_mode(not self.sel_mode)

    def start_import(self):
        app = App.get_running_app()
        app.start_pick(app.current_album, app.kind)

    def del_folder(self):
        app = App.get_running_app()
        album = app.current_album
        if not album:
            return

        def go():
            app.store.delete_album(album)
            app.current_album = None
            app.go("home")
        ask("Delete folder", f"Delete folder '{album}'?\n(Files stay in the vault)", go, "Delete")

    def more(self):
        self.limit += self.PAGE
        self.refresh(False)

    def refresh(self, reset=True):
        app = App.get_running_app()
        st, album, kind = app.store, app.current_album, app.kind
        if reset:
            self.limit = self.PAGE
        self.grid.clear_widgets()
        self.tiles = {}
        if st.key is None:
            return
        self.names = st.album_items(album, kind) if album else st.media_list(kind)
        word = "Photos" if kind == "photo" else "Videos"
        n = len(self.names)
        if self.sel_mode:
            self.title.text = f"({len(self.sel)}) Selected"
        else:
            self.title.text = album if album else f"All {word}"
        self.count.text = f"{n} files" if n else "Empty"
        self.imp.text = f"Import {word}  +"
        self._show(self.imp_box, bool(album) and not self.sel_mode, dp(66))
        can_del = bool(album) and album not in DEFAULT_FOLDERS and not self.sel_mode
        self.del_btn.width, self.del_btn.opacity, self.del_btn.disabled = \
            (dp(52), 1, False) if can_del else (0, 0, True)
        app.gen += 1
        gen = app.gen
        self.grid.row_default_height = (Window.width - dp(8) - dp(12)) / 3
        for i, name in enumerate(self.names[:self.limit]):
            t = make_tile(app, name, gen)
            t.bind(on_release=lambda w, i=i, name=name: self.tap(i, name))
            self.tiles[name] = t
            if name in self.sel:
                mark(t, True)
            self.grid.add_widget(t)
        if n > self.limit:
            self.grid.add_widget(btn(f"+{n - self.limit}\nmore", self.more, SOFT, radius=10))

    def tap(self, i, name):
        if self.sel_mode:
            if name in self.sel:
                self.sel.discard(name)
            else:
                self.sel.add(name)
            mark(self.tiles[name], name in self.sel)
            self.title.text = f"({len(self.sel)}) Selected"
        else:
            App.get_running_app().open_viewer(self.names, i)

    # ----- actions on selected files -----
    def _need_sel(self):
        if not self.sel:
            info("Select files", "Tap the files first.", .28)
            return False
        return True

    def do_delete(self):
        if not self._need_sel():
            return

        def go(perm):
            st = App.get_running_app().store
            for n in list(self.sel):
                try:
                    st.delete(n) if perm else st.trash_move(n)
                except Exception:
                    pass
            self.set_select_mode(False)
        delete_dialog(len(self.sel), go)

    def do_unhide(self):
        if not self._need_sel():
            return
        names = list(self.sel)
        ask("Unhide", "Selected files will go back to your phone Gallery\n"
            "and be removed from the vault. Continue?",
            lambda: App.get_running_app().unhide(names, lambda: self.set_select_mode(False)),
            "Unhide")

    def do_move(self):
        if not self._need_sel():
            return
        app = App.get_running_app()
        src = app.current_album
        folders = [f for f in app.store.folders_for(app.kind) if f != src]

        def go(dst):
            for n in list(self.sel):
                app.store.move_to_album(n, src, dst)
            self.set_select_mode(False)
        folder_picker("Move to folder", folders, go)


# ---------------- Viewer ----------------
class ViewerScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        self.names, self.i, self.vid, self.tok = [], 0, None, 0
        root = BoxLayout(orientation="vertical")
        self.pos_lbl = Label(font_size=dp(16), bold=True)
        root.add_widget(header(self.pos_lbl, [
            btn("Back", lambda: app.go("folder"), SOFT, size_hint_x=None, width=dp(70))]))
        self.stage = SwipeStage(self.step)
        root.add_widget(self.stage)
        bar = BoxLayout(size_hint_y=None, height=dp(60), spacing=dp(5), padding=dp(6))
        bar.add_widget(btn("<", lambda: self.step(-1), SOFT, size_hint_x=.6))
        self.play_btn = btn("Pause", self.toggle_play, SOFT, font_size=dp(12))
        bar.add_widget(self.play_btn)
        bar.add_widget(btn("+Folder", self.pick_album, ACCENT, font_size=dp(12)))
        bar.add_widget(btn("Unhide", self.restore, GREEN, font_size=dp(12)))
        bar.add_widget(btn("Delete", self.delete, RED, font_size=dp(12)))
        bar.add_widget(btn(">", lambda: self.step(1), SOFT, size_hint_x=.6))
        root.add_widget(bar)
        self.add_widget(root)

    def show(self, names, i):
        self.names, self.i = names, i
        self.load()

    def stop_video(self):
        if self.vid is not None:
            try:
                self.vid.state = "stop"
                self.vid.unload()
            except Exception:
                pass
            self.vid = None

    def on_leave(self, *a):
        self.tok += 1
        self.stop_video()
        self.stage.clear_widgets()

    def load(self):
        app = App.get_running_app()
        self.tok += 1
        tok = self.tok
        self.stop_video()
        self.stage.clear_widgets()
        if not self.names:
            app.go("folder")
            return
        self.i %= len(self.names)
        name = self.names[self.i]
        self.pos_lbl.text = f"{self.i + 1} / {len(self.names)}"
        vid = is_video(name)
        self.play_btn.disabled = not vid
        self.play_btn.text = "Pause" if vid else "-"
        self.stage.add_widget(Label(text="Decrypting..."))
        key = app.store.key

        def work():
            try:
                p, err = app.store.decrypt_cached(name, key), None
            except Exception as e:
                p, err = None, str(e)
            Clock.schedule_once(lambda dt: self.ready(tok, name, p, err))
        threading.Thread(target=work, daemon=True).start()

    def ready(self, tok, name, path, err):
        if tok != self.tok:
            return
        self.stage.clear_widgets()
        if err:
            self.stage.add_widget(Label(text="Could not open: " + err[:100]))
            return
        if is_video(name):
            if not video_provider_ok():
                self.show_external(path, "ffpyplayer not available: " + (FFPY_ERR or "no video engine"))
                return
            try:
                self.vid = Video(source=path, state="play", options={"eos": "loop"})
                self.stage.add_widget(self.vid)
                self.play_btn.disabled = False
                self.play_btn.text = "Pause"
                Clock.schedule_once(lambda dt: self.check_video(tok, path), 4)
            except Exception as e:
                self.show_external(path, "Player error: " + str(e)[:80])
            return
        self.stage.add_widget(Image(source=path))

    def check_video(self, tok, path):
        """If the in-app player showed nothing after 4 sec, offer the phone's player."""
        if tok != self.tok or self.vid is None:
            return
        if not self.vid.texture:
            self.stop_video()
            self.stage.clear_widgets()
            self.show_external(path, "ffpyplayer started but showed no picture")

    def show_external(self, path, why=""):
        """Videos always play in the phone's own video player."""
        self.play_btn.disabled = True
        self.play_btn.text = "-"
        box = BoxLayout(orientation="vertical", spacing=dp(14), padding=dp(24))
        msg = Label(text="Tap the button to play this video\n"
                         "in your phone's video player.\n\n" + (why and "[Reason] " + why),
                    halign="center", color=hx(TXT))
        msg.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        box.add_widget(msg)
        box.add_widget(btn("Open in video player", lambda: self.open_external(path), GREEN,
                           size_hint_y=None, height=dp(60), radius=26))
        box.add_widget(Widget(size_hint_y=.4))
        self.stage.add_widget(box)

    def open_external(self, path):
        app = App.get_running_app()
        app.busy = True
        app.external = True
        lbl = Label(text="Preparing video...", halign="center")
        pop = popup("Video", lbl, (.8, .25), auto_dismiss=False)
        pop.open()

        def done(ok, err):
            pop.dismiss()
            app.last = time.time()
            if not ok:
                app.busy = False
                app.external = False
                info("Cannot play", "No video player could be started.\n" + err[:100], .4)

        def work():
            ok, err = False, ""
            try:
                os.makedirs(PLAY_DIR, exist_ok=True)
                open(os.path.join(PLAY_DIR, ".nomedia"), "a").close()
                dst = os.path.join(PLAY_DIR, os.path.basename(path))
                if not (os.path.exists(dst) and os.path.getsize(dst) > 0):
                    shutil.copyfile(path, dst)
                ok, err = play_external(dst)
            except Exception as e:
                err = str(e)
            Clock.schedule_once(lambda dt: done(ok, err))

        threading.Thread(target=work, daemon=True).start()

    def toggle_play(self):
        if self.vid is None:
            return
        if self.vid.state == "play":
            self.vid.state = "pause"
            self.play_btn.text = "Play"
        else:
            self.vid.state = "play"
            self.play_btn.text = "Pause"

    def step(self, d):
        if not self.names:
            return
        self.i += d
        self.load()

    def delete(self):
        if not self.names:
            return
        name = self.names[self.i]

        def go(perm):
            st = App.get_running_app().store
            self.stop_video()
            try:
                st.delete(name) if perm else st.trash_move(name)
            except Exception:
                pass
            if name in self.names:
                self.names.remove(name)
            self.load()
        delete_dialog(1, go)

    def restore(self):
        if not self.names:
            return
        app = App.get_running_app()
        name = self.names[self.i]

        def go():
            self.stop_video()

            def after():
                if name in self.names:
                    self.names.remove(name)
                self.load()
            app.unhide([name], after)
        ask("Unhide", "This file will go back to your phone Gallery\nand be removed from the vault. "
            "Continue?", go, "Unhide")

    def pick_album(self):
        if not self.names:
            return
        app = App.get_running_app()
        name = self.names[self.i]
        kind = "video" if is_video(name) else "photo"

        def add(a):
            app.store.add_to_album(a, name)
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        pop = popup("Add to Folder", box, (.85, .6))
        for a in app.store.folders_for(kind):
            box.add_widget(btn(a, lambda a=a: (add(a), pop.dismiss())))
        box.add_widget(btn("+ New Folder", lambda: (
            pop.dismiss(), new_album_popup(add, kind)), ACCENT))
        pop.open()


# ---------------- In-app phone gallery (used when the system picker is unavailable, e.g. Pydroid) ----------------
class ImportScreen(Screen):
    PAGE = 60

    def __init__(self, **kw):
        super().__init__(**kw)
        app = App.get_running_app()
        self.move = True
        self.items, self.sel, self.tiles = [], set(), {}
        self.limit, self.filt, self.scan_id, self.gen = self.PAGE, "all", 0, 0
        root = BoxLayout(orientation="vertical")
        root.add_widget(header(title_label("Phone Gallery"), [
            btn("Back", app.show_folder, SOFT, size_hint_x=None, width=dp(70))], 64))
        frow = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8), padding=(dp(10), dp(4)))
        self.fbtns = {}
        for label, k in [("All", "all"), ("Photos", "photo"), ("Videos", "video")]:
            b = btn(label, lambda k=k: self.set_filter(k), radius=18)
            self.fbtns[k] = b
            frow.add_widget(b)
        root.add_widget(frow)
        self.status = Label(size_hint_y=None, height=dp(40), font_size=dp(13),
                            halign="center", color=hx(MUTED))
        self.status.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        root.add_widget(self.status)
        sv = ScrollView()
        self.grid = GridLayout(cols=3, spacing=dp(6), padding=dp(4),
                               size_hint_y=None, row_force_default=True)
        self.grid.bind(minimum_height=self.grid.setter("height"))
        sv.add_widget(self.grid)
        root.add_widget(sv)
        row1 = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6), padding=(dp(8), 0))
        row1.add_widget(btn("Select All", self.select_all))
        row1.add_widget(btn("Clear", self.clear_sel))
        self.move_btn = btn("Move: ON", self.toggle_move, RED)
        row1.add_widget(self.move_btn)
        root.add_widget(row1)
        row2 = BoxLayout(size_hint_y=None, height=dp(60), padding=(dp(8), dp(6)))
        self.imp = btn("Import  +", self.do_import, GREEN)
        row2.add_widget(self.imp)
        root.add_widget(row2)
        self.add_widget(root)

    def on_pre_enter(self, *a):
        app = App.get_running_app()
        self.sel.clear()
        self.limit = self.PAGE
        self.filt = "video" if app.kind == "video" else "photo"
        self.scan()

    def scan(self):
        self.scan_id += 1
        sid = self.scan_id
        self.status.text = "Loading your gallery..."
        self.grid.clear_widgets()

        def work():
            found, stack = [], [(ROOT, 0)]
            while stack and len(found) < 6000:
                d, depth = stack.pop()
                try:
                    names = os.listdir(d)
                except Exception:
                    continue
                for n in names:
                    if n.startswith("."):
                        continue
                    p = os.path.join(d, n)
                    low = n.lower()
                    if low.endswith(ALL_EXTS):
                        try:
                            found.append((p, os.path.getmtime(p)))
                        except Exception:
                            pass
                    elif depth < 6 and low != "android" and os.path.isdir(p):
                        stack.append((p, depth + 1))
            found.sort(key=lambda x: x[1], reverse=True)
            Clock.schedule_once(lambda dt: self.scanned(sid, found))
        threading.Thread(target=work, daemon=True).start()

    def scanned(self, sid, found):
        if sid != self.scan_id:
            return
        self.items = [p for p, m in found]
        self.draw()

    def visible(self):
        if self.filt == "photo":
            return [p for p in self.items if not is_video(p)]
        if self.filt == "video":
            return [p for p in self.items if is_video(p)]
        return self.items

    def set_filter(self, k):
        self.filt, self.limit = k, self.PAGE
        self.draw()

    def draw(self):
        app = App.get_running_app()
        self.grid.clear_widgets()
        self.tiles = {}
        self.gen += 1
        gen = self.gen
        for k, b in self.fbtns.items():
            b.set_bg(ACCENT if k == self.filt else CARD)
        vis = self.visible()
        if not vis:
            self.status.text = ("No files found.\nAllow 'All files access' for Pydroid 3 in "
                                "phone Settings, then reopen.")
        else:
            self.status.text = f"{len(vis)} files  |  {len(self.sel)} selected"
        self.grid.row_default_height = (Window.width - dp(20)) / 3
        pairs = []
        for p in vis[:self.limit]:
            if is_video(p):
                t = RBtn(text="VIDEO\n" + os.path.basename(p)[:14], bg=SOFT, radius=10,
                         halign="center", font_size=dp(11))
            else:
                t = Thumb()
                pairs.append((t, p))
            t.bind(on_release=lambda w, p=p: self.toggle(p))
            self.tiles[p] = t
            if p in self.sel:
                mark(t, True)
            self.grid.add_widget(t)
        if len(vis) > self.limit:
            self.grid.add_widget(btn(f"+{len(vis) - self.limit}\nmore", self.more, SOFT, radius=10))
        self.imp.text = f"Import  +  ({len(self.sel)})" if self.sel else "Import  +"

        def work():
            for t, p in pairs:
                if gen != self.gen:
                    return
                tp = self.make_thumb(p)
                if tp:
                    Clock.schedule_once(lambda dt, t=t, tp=tp: setattr(t, "source", tp)
                                        if gen == self.gen else None)
        threading.Thread(target=work, daemon=True).start()

    def make_thumb(self, p):
        try:
            cdir = App.get_running_app().store.cache
            tp = os.path.join(cdir, "g_" + hashlib.md5(p.encode()).hexdigest()[:16] + ".jpg")
            if os.path.exists(tp):
                return tp
            if PILImage is None:
                return p
            im = PILImage.open(p)
            try:
                im.draft("RGB", (400, 400))
            except Exception:
                pass
            im = im.convert("RGB")
            im.thumbnail((300, 300))
            im.save(tp, "JPEG", quality=75)
            return tp
        except Exception:
            return None

    def more(self):
        self.limit += self.PAGE
        self.draw()

    def toggle(self, p):
        if p in self.sel:
            self.sel.discard(p)
        else:
            self.sel.add(p)
        if p in self.tiles:
            mark(self.tiles[p], p in self.sel)
        vis = self.visible()
        self.status.text = f"{len(vis)} files  |  {len(self.sel)} selected"
        self.imp.text = f"Import  +  ({len(self.sel)})" if self.sel else "Import  +"

    def select_all(self):
        self.sel.update(self.visible()[:self.limit])
        self.draw()

    def clear_sel(self):
        self.sel.clear()
        self.draw()

    def toggle_move(self):
        self.move = not self.move
        self.move_btn.text = "Move: ON" if self.move else "Move: OFF"
        self.move_btn.set_bg(RED if self.move else CARD)

    def do_import(self):
        files = [p for p in self.items if p in self.sel and os.path.isfile(p)]
        if not files:
            info("Select files", "Tap the photos/videos you want to import.", .28)
            return
        app = App.get_running_app()
        self.sel.clear()
        app.run_import(files, app.current_album, uri=False, move=self.move)


# ---------------- App ----------------
class VaultApp(App):
    current_album = None
    kind = "photo"
    gen = 0
    busy = False
    external = False
    last = 0

    def build(self):
        self.title = "Private Gallery Vault"
        base = os.path.join(self.user_data_dir, "vault")
        os.makedirs(base, exist_ok=True)
        self.store = Store(base, os.path.join(self.user_data_dir, "vault_cache"))
        clear_play_dir()
        apply_theme(self.store.cfg.get("theme", "light"), self.store.cfg.get("scheme", 0))
        self.thumb_q = queue.Queue()
        for _ in range(2):
            threading.Thread(target=self.thumb_worker, daemon=True).start()
        self.last = time.time()
        self.sm = ScreenManager(transition=SlideTransition(duration=.2))
        self.build_screens("pin")
        Window.bind(on_keyboard=self.on_key, on_touch_down=self.touched)
        Clock.schedule_interval(self.tick, 1)
        return self.sm

    def build_screens(self, cur):
        tr = self.sm.transition
        self.sm.transition = NoTransition()
        self.sm.clear_widgets()
        for cls, name in [(PinScreen, "pin"), (HomeScreen, "home"), (FolderScreen, "folder"),
                          (ViewerScreen, "viewer"), (ImportScreen, "import"),
                          (SettingsScreen, "settings"), (ChangePinScreen, "changepin"),
                          (ThemeScreen, "theme"), (FolderStyleScreen, "folderstyle"),
                          (LanguageScreen, "language"), (TrashScreen, "trash")]:
            self.sm.add_widget(cls(name=name))
        self.sm.current = cur
        self.sm.transition = tr

    def restyle(self, cur):
        """Re-applies theme colours by rebuilding all screens."""
        apply_theme(self.store.cfg.get("theme", "light"), self.store.cfg.get("scheme", 0))
        self.build_screens(cur)

    def on_start(self):
        ask_android_permissions()

    # ----- navigation -----
    def go(self, name):
        self.sm.current = name

    def open_folder(self, album):
        self.current_album = album
        self.go("folder")

    def show_folder(self):
        if self.sm.current == "folder":
            self.sm.get_screen("folder").refresh()
        else:
            self.go("folder")

    def open_viewer(self, names, i):
        self.sm.get_screen("viewer").show(list(names), i)
        self.sm.current = "viewer"

    # ----- thumbnails -----
    def thumb_worker(self):
        while True:
            gen, w, name, key = self.thumb_q.get()
            if gen != self.gen or self.store.key is None:
                continue
            try:
                p = self.store.thumb_cached(name, key)
            except Exception:
                continue
            if not p:
                continue
            Clock.schedule_once(lambda dt, w=w, p=p, gen=gen: self.set_thumb(gen, w, p))

    def set_thumb(self, gen, w, p):
        if gen == self.gen and self.store.key is not None:
            w.source = p

    # ----- import: phone gallery -> vault folder -----
    def start_pick(self, album, kind, mode="gallery"):
        """Opens the device gallery. After selecting, an 'Import +' button imports directly."""
        if self.store.key is None:
            return
        self.busy = True

        def got(uris):
            Clock.schedule_once(lambda dt: self._picked(uris, album, kind))

        if open_gallery_picker(got, kind, mode):
            return
        self.busy = False
        info("Import", "Gallery could not start.\n" + PICK_DBG, .4)
        self.go("import")  # fallback browser

    def _pick_failed(self, album, kind):
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
        lbl = Label(text="Nothing was selected.\n[" + PICK_DBG + "]", halign="center")
        lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        box.add_widget(lbl)
        pop = popup("Import", box, (.88, .42))
        row = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(52))
        row.add_widget(btn("Close", pop.dismiss, SOFT))
        row.add_widget(btn("Try Files picker", lambda: (
            pop.dismiss(), self.start_pick(album, kind, "document")), GREEN))
        box.add_widget(row)
        pop.open()

    def _picked(self, uris, album, kind="photo"):
        self.last = time.time()
        if not uris or self.store.key is None:
            self.busy = False
            if self.store.key is not None:
                self._pick_failed(album, kind)
            return
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
        lbl = Label(text=f"{len(uris)} file(s) selected", halign="center", font_size=dp(16))
        box.add_widget(lbl)
        pop = popup("Import", box, (.85, .3), auto_dismiss=False)

        def cancel():
            pop.dismiss()
            self.busy = False

        def go():
            pop.dismiss()
            self.run_import(uris, album, uri=True)
        row = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(52))
        row.add_widget(btn("Cancel", cancel, SOFT))
        row.add_widget(btn("Import  +", go, GREEN))
        box.add_widget(row)
        pop.open()

    def run_import(self, items, album, uri=False, move=False):
        key, total = self.store.key, len(items)
        if key is None or not total:
            self.busy = False
            return
        tmpdir = os.path.join(self.user_data_dir, "tmp_import")
        lbl = Label(text=f"Importing 0/{total}", halign="center")
        pop = popup("Encrypting + importing...", lbl, (.8, .25), auto_dismiss=False)
        pop.open()
        self.busy = True

        def show_done(ok, err, names, declined):
            self.busy = False
            self.last = time.time()
            if not ok:
                info("Import failed", ("Error: " + err[:120]) if err else "Nothing was imported.", .35)
                self.show_folder()
                return
            vids = sum(1 for n in names if is_video(n))
            if vids == len(names):
                word = "video" if ok == 1 else "videos"
            elif vids == 0:
                word = "photo" if ok == 1 else "photos"
            else:
                word = "file" if ok == 1 else "files"
            extra = []
            if ok < total:
                extra.append(f"{total - ok} failed. " + err[:80])
            if declined:
                extra.append("Originals are still in your Gallery\n(delete was not allowed).")
            success_popup(f"{ok} {word} imported successfully", "\n".join(extra), self.show_folder)

        def finish(ok, err, names, ok_uris, ok_paths):
            pop.dismiss()
            self.last = time.time()
            if ok and uri and ok_uris:
                # remove the originals from the phone Gallery (Android asks once)
                def after_del(deleted):
                    Clock.schedule_once(lambda dt: show_done(ok, err, names, not deleted))
                if not delete_from_gallery(ok_uris, after_del):
                    show_done(ok, err, names, True)
                return
            if ok_paths:
                media_rescan(ok_paths)
            show_done(ok, err, names, False)

        def work():
            ok, last_err, names, ok_uris, ok_paths = 0, "", [], [], []
            for i, it in enumerate(items):
                tmp = None
                try:
                    tmp = copy_uri_to_file(it, tmpdir, i) if uri else it
                    nm, e = self.store.add_one(tmp, i, uri or move, key)
                except Exception as ex:
                    nm, e = None, str(ex)
                if nm:
                    ok += 1
                    names.append(nm)
                    if uri:
                        ok_uris.append(it)
                    elif move:
                        ok_paths.append(it)
                else:
                    last_err = e or ""
                    if uri and tmp:
                        _rm(tmp)
                Clock.schedule_once(
                    lambda dt, i=i: setattr(lbl, "text", f"Importing {i + 1}/{total}"))
            if album and names:
                try:
                    self.store.add_many_to_album(album, names)
                except Exception:
                    pass
            Clock.schedule_once(lambda dt: finish(ok, last_err, names, ok_uris, ok_paths))

        threading.Thread(target=work, daemon=True).start()

    # ----- unhide: vault -> phone gallery -----
    def unhide(self, names, after):
        key = self.store.key
        lbl = Label(text="Unhiding...", halign="center")
        pop = popup("Unhide", lbl, (.8, .25), auto_dismiss=False)
        pop.open()
        self.busy = True

        def done(ok, err):
            pop.dismiss()
            self.busy = False
            self.last = time.time()
            if ok:
                info("Done", f"{ok} file(s) are back in the Gallery (VaultRestored folder) "
                     "and removed from the vault.\nThey may take a moment to show up.", .4)
            if err:
                info("Unhide failed", err[:150], .35)
            after()

        def work():
            ok, err = 0, ""
            for i, n in enumerate(names):
                try:
                    self.store.restore_file(n, key)
                    self.store.delete(n)
                    ok += 1
                except Exception as e:
                    err = str(e)
                Clock.schedule_once(
                    lambda dt, i=i: setattr(lbl, "text", f"Unhiding {i + 1}/{len(names)}"))
            Clock.schedule_once(lambda dt: done(ok, err))

        threading.Thread(target=work, daemon=True).start()

    # ----- lock / unlock -----
    def after_unlock(self):
        self.last = time.time()
        try:
            self.store.purge_trash()
        except Exception:
            pass
        self.kind = "photo"
        if self.store.plain_files():
            self.migrate()
        else:
            self.go("home")

    def migrate(self):
        st = self.store
        self.busy = True
        lbl = Label(text="Encrypting old files...", halign="center")
        pop = popup("Encrypting...", lbl, (.8, .25), auto_dismiss=False)
        pop.open()

        def done(dt):
            pop.dismiss()
            self.busy = False
            self.last = time.time()
            self.go("home")

        def cb(i, n):
            Clock.schedule_once(lambda dt: setattr(lbl, "text", f"Encrypting {i}/{n}"))

        def work():
            try:
                st.migrate_plain(cb)
            except Exception:
                pass
            Clock.schedule_once(done)

        threading.Thread(target=work, daemon=True).start()

    def lock(self):
        """Locks the vault: forgets the key, clears decrypted cache, shows the PIN screen."""
        try:
            self.sm.get_screen("viewer").stop_video()
        except Exception:
            pass
        self.gen += 1
        self.store.key = None
        self.store.clear_cache()
        clear_play_dir()
        self.current_album = None
        self.busy = False
        self.external = False
        self.go("pin")

    def touched(self, *a):
        self.last = time.time()
        return False

    def tick(self, dt):
        if self.store.key is None or self.busy:
            return
        sec = self.store.cfg.get("autolock", 60)
        if sec and time.time() - self.last > sec:
            self.lock()

    def on_pause(self):
        return True

    def on_resume(self):
        # coming back from the phone's video player
        if self.external:
            self.external = False
            self.busy = False
        self.last = time.time()

    def on_key(self, win, key, *a):
        if key != 27:  # Back button
            return False
        if self.busy:
            return True
        cur = self.sm.current
        if cur == "pin":
            return False
        if cur == "home":
            self.lock()
        elif cur == "folder":
            self.sm.get_screen("folder").back()
        elif cur == "viewer":
            self.go("folder")
        elif cur == "import":
            self.show_folder()
        elif cur == "changepin":
            self.go("settings")
        elif cur in ("settings", "folderstyle"):
            self.go("home")
        elif cur in ("theme", "language", "trash"):
            self.go("settings")
        return True


if __name__ == "__main__":
    VaultApp().run()
